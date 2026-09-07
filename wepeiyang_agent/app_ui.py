"""Observed, app-scoped ADB navigation shared by CLI and the agent."""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

from .forum import COMPONENT, PACKAGE
from .parser import parse_bounds


UNSAFE_LABEL = re.compile(
    r"发帖|发布|回复|评论|点赞|点踩|收藏|投票|举报|删除|支付|付款|购买|充值|转账|"
    r"提交|发送|签到|打卡|选课|退课|退选|报名|预约|注销|退出登录|登出|绑定|解绑|"
    r"修改密码|重置密码|登录|验证码|授权|允许|确认|确定|保存|同意|开通|续费|"
    r"(?i:submit|send|delete|purchase|payment|sign.in|log.in|sign.out|like|vote|reply)"
)


class AppNavigator:
    def __init__(self, forum, data_dir, trace):
        self.forum, self.adb, self.trace = forum, forum.adb, trace
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / "app-screen.json"

    def _foreground(self):
        if self.adb.foreground_package() != PACKAGE:
            raise ValueError("当前前台不是微北洋/天外天；可调用 app.open。其他 App 或系统弹窗需用户处理。")

    def _nodes(self, xml):
        result = []
        for element in ET.fromstring(xml).iter("node"):
            attr = element.attrib
            if attr.get("package") != PACKAGE:
                continue
            bounds = parse_bounds(attr.get("bounds", ""))
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                continue
            result.append({"node_id": "n" + str(len(result)),
                "text": attr.get("content-desc") or attr.get("text", ""),
                "bounds": list(bounds), "class": attr.get("class", ""),
                "resource_id": attr.get("resource-id", ""),
                "clickable": attr.get("clickable") == "true",
                "scrollable": attr.get("scrollable") == "true",
                "selected": attr.get("selected") == "true",
                "enabled": attr.get("enabled", "true") == "true",
                "password": attr.get("password") == "true"})
        if not result:
            raise ValueError("无法读取天外天页面节点，未执行操作。")
        return result

    @staticmethod
    def _fingerprint(nodes):
        return hashlib.sha256(json.dumps(nodes, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def observe(self):
        self._foreground()
        xml = self.adb.hierarchy()
        nodes = self._nodes(xml)
        self._foreground()
        png = self.adb.screenshot()
        self._foreground()
        with Image.open(io.BytesIO(png)) as image:
            width, height = image.size
        screen_id = uuid.uuid4().hex
        folder = self.trace.directory / "app"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (screen_id + ".png")
        path.write_bytes(png)
        path.with_suffix(".xml").write_bytes(xml)
        state = {"screen_id": screen_id, "captured_at": time.time(), "package": PACKAGE,
                 "width": width, "height": height, "image": str(path), "nodes": nodes,
                 "fingerprint": self._fingerprint(nodes)}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.state_path)
        return state

    def _fresh(self, screen_id):
        if not self.state_path.exists():
            raise ValueError("先 app.observe 获取当前 screen_id，不能盲点。")
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state["screen_id"] != screen_id or not 0 <= time.time() - state["captured_at"] <= 300:
            raise ValueError("页面快照已过期，先重新 app.observe。")
        self._foreground()
        nodes = self._nodes(self.adb.hierarchy())
        if self._fingerprint(nodes) != state["fingerprint"]:
            raise ValueError("页面已经变化，先重新 app.observe，禁止使用旧坐标。")
        self._foreground()
        return state

    @staticmethod
    def _point(state, x, y):
        if not 0 <= x < state["width"] or not 0 <= y < state["height"]:
            raise ValueError("坐标超出截图范围。")
        hits = [node for node in state["nodes"] if
                node["bounds"][0] <= x < node["bounds"][2] and node["bounds"][1] <= y < node["bounds"][3]]
        if not hits:
            raise ValueError("坐标不在天外天页面区域内。")
        return hits

    @staticmethod
    def _read_only_target(state, hits):
        controls = [node for node in hits if node["clickable"]]
        if not controls:
            raise ValueError("该区域没有可观察到的可点击控件；请重新观察，不盲点。")
        target = min(controls, key=lambda n: (n["bounds"][2] - n["bounds"][0]) * (n["bounds"][3] - n["bounds"][1]))
        x1, y1, x2, y2 = target["bounds"]
        children = [n for n in state["nodes"] if x1 <= n["bounds"][0] and y1 <= n["bounds"][1]
                    and n["bounds"][2] <= x2 and n["bounds"][3] <= y2]
        labels = "\n".join(n["text"] + " " + n["resource_id"] for n in children)
        if not target["enabled"] or any(n["password"] or "EditText" in n["class"] or
            any(t in n["class"] for t in ("Switch", "CheckBox", "RadioButton")) for n in children):
            raise ValueError("只读导航不操作输入框、密码、开关或勾选控件。")
        if UNSAFE_LABEL.search(labels):
            raise ValueError("该区域可能涉及提交、互动或账号变更；只读导航已拦截。")
        cards = [n for n in state["nodes"] if re.search(r"#MP\d+", n["text"])]
        for card in cards:
            a, b, c, d = card["bounds"]
            if a <= x1 and b <= y1 and x2 <= c and y2 <= d and target != card:
                raise ValueError("帖子内的头像、互动图标不能通过通用导航点击；读帖子使用 forum.detail。")
        if cards and not target["text"] and y1 > state["height"] * .1 and y2 < state["height"] * .9:
            raise ValueError("论坛中的无标签浮动按钮用途不明，禁止点击。")
        return target

    def invoke(self, action, args):
        if action == "observe":
            return self.observe()
        if action == "open":
            # Bring forward without force-stop or clearing the user's current session.
            self.state_path.unlink(missing_ok=True)
            (self.data_dir / "forum-cursor.json").unlink(missing_ok=True)
            self.adb.shell("am", "start", "-W", "-n", COMPONENT, timeout=45)
            self.forum._sleep()
            return self.observe()
        state = self._fresh(args["screen_id"])
        if action == "tap":
            if "node_id" in args:
                target = next((n for n in state["nodes"] if n["node_id"] == args["node_id"]), None)
                if target is None:
                    raise ValueError("node_id 不在这次页面快照中。")
                x1, y1, x2, y2 = target["bounds"]
                x, y = (x1 + x2) // 2, (y1 + y2) // 2
            else:
                x, y = args["x"], args["y"]
            hits = self._point(state, x, y)
            self._read_only_target(state, hits)
            self._foreground()
            self.state_path.unlink(missing_ok=True)
            self.adb.tap(x, y)
        elif action == "swipe":
            width, height = state["width"], state["height"]
            # Scroll within observed scrollable content, not OS notification/edge gestures.
            containers = [n for n in state["nodes"] if n["scrollable"]]
            if not containers:
                raise ValueError("未发现可滚动区域，请观察页面或返回。")
            if args.get("node_id"):
                container = next((n for n in containers if n["node_id"] == args["node_id"]), None)
                if container is None:
                    raise ValueError("指定 node_id 不是可滚动区域。")
            else:
                container = max(containers, key=lambda n: (n["bounds"][2] - n["bounds"][0]) * (n["bounds"][3] - n["bounds"][1]))
            left, top, right, bottom = container["bounds"]
            left, top, right, bottom = max(1, left), max(1, top), min(width - 1, right), min(height - 1, bottom)
            cx, cy = (left + right) // 2, (top + bottom) // 2
            dx, dy = round((right - left) * .3), round((bottom - top) * .3)
            points = {"up": (cx, cy + dy, cx, cy - dy), "down": (cx, cy - dy, cx, cy + dy),
                      "left": (cx + dx, cy, cx - dx, cy), "right": (cx - dx, cy, cx + dx, cy)}
            self._foreground()
            self.state_path.unlink(missing_ok=True)
            self.adb.swipe(*points[args["direction"]], duration_ms=650)
        elif action == "back":
            self._foreground()
            self.state_path.unlink(missing_ok=True)
            self.adb.shell("input", "keyevent", "4")
        else:
            raise ValueError("未知 App 操作。")
        # Invalidate the forum cursor and consumed snapshot even when observation fails.
        self.state_path.unlink(missing_ok=True)
        (self.data_dir / "forum-cursor.json").unlink(missing_ok=True)
        self.forum._sleep()
        if self.adb.foreground_package() != PACKAGE:
            return {"action": action, "sent": True, "in_app": False,
                    "requires_user": True, "message": "操作后离开天外天或出现系统弹窗，已停止采集；可 app.open 或请用户处理。"}
        result = self.observe()
        return {**result, "action": action, "sent": True, "in_app": True,
                "page_changed": result["fingerprint"] != state["fingerprint"]}
