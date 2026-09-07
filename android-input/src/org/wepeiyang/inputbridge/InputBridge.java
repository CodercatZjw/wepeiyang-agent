package org.wepeiyang.inputbridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.inputmethodservice.InputMethodService;
import android.util.Base64;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import java.nio.charset.StandardCharsets;

/** No network/storage permissions; only the ADB shell may send text. */
public final class InputBridge extends InputMethodService {
    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            EditorInfo info = getCurrentInputEditorInfo();
            InputConnection connection = getCurrentInputConnection();
            if (info == null || connection == null || !"com.twt.service".equals(info.packageName)) {
                setResultCode(0);
                setResultData("No active TianWaiTian editor");
                return;
            }
            try {
                String text = new String(Base64.decode(intent.getStringExtra("text_b64"), Base64.DEFAULT), StandardCharsets.UTF_8);
                if (text.length() < 1 || text.length() > 100 || text.matches("(?s).*[\\p{Cntrl}].*"))
                    throw new IllegalArgumentException("Invalid text");
                connection.beginBatchEdit();
                connection.performContextMenuAction(android.R.id.selectAll);
                boolean accepted = connection.commitText(text, 1);
                connection.endBatchEdit();
                setResultCode(accepted ? 1 : 0);
                setResultData(accepted ? "Text committed" : "Input rejected");
            } catch (Exception error) {
                setResultCode(0);
                setResultData("Invalid input");
            }
        }
    };
    @Override public void onCreate() {
        super.onCreate();
        registerReceiver(receiver, new IntentFilter("org.wepeiyang.inputbridge.INPUT"), "android.permission.DUMP", null);
    }
    @Override public void onDestroy() {
        unregisterReceiver(receiver);
        super.onDestroy();
    }
}
