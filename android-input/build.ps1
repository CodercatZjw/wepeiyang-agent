param([string]$Sdk = 'D:\Android\SDK', [string]$Jdk = 'C:\Program Files\Android\Android Studio\jbr')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$buildDir = Join-Path $projectRoot 'work\input-build'
$assetDir = Join-Path $projectRoot 'wepeiyang_agent\assets'
New-Item -ItemType Directory -Force $buildDir, $assetDir, "$buildDir\classes", "$buildDir\dex" | Out-Null
$buildTools = Join-Path $Sdk 'build-tools\36.1.0'
$platform = Join-Path $Sdk 'platforms\android-34\android.jar'
$env:JAVA_HOME = $Jdk
$env:PATH = "$Jdk\bin;$env:PATH"
function Assert-Success { if ($LASTEXITCODE -ne 0) { throw "Build failed: $LASTEXITCODE" } }
& "$buildTools\aapt2.exe" compile --dir "$PSScriptRoot\res" -o "$buildDir\resources.zip"
Assert-Success
& "$buildTools\aapt2.exe" link -I $platform --manifest "$PSScriptRoot\AndroidManifest.xml" -o "$buildDir\unsigned.apk" "$buildDir\resources.zip"
Assert-Success
& "$Jdk\bin\javac.exe" -encoding UTF-8 -source 8 -target 8 -classpath $platform -d "$buildDir\classes" "$PSScriptRoot\src\org\wepeiyang\inputbridge\InputBridge.java"
Assert-Success
$classFiles = @(Get-ChildItem "$buildDir\classes" -Recurse -Filter '*.class' | ForEach-Object FullName)
& "$buildTools\d8.bat" --lib $platform --min-api 23 --output "$buildDir\dex" @classFiles
Assert-Success
& "$Jdk\bin\jar.exe" uf "$buildDir\unsigned.apk" -C "$buildDir\dex" classes.dex
Assert-Success
$keyStore = Join-Path $buildDir 'development.keystore'
if (-not (Test-Path -LiteralPath $keyStore)) {
    & "$Jdk\bin\keytool.exe" -genkeypair -keystore $keyStore -storepass android -keypass android -alias wpy-development -dname 'CN=WPY Agent Development' -keyalg RSA -keysize 2048 -validity 3650
    Assert-Success
}
& "$buildTools\apksigner.bat" sign --ks $keyStore --ks-pass pass:android --key-pass pass:android --out "$assetDir\wpy-input.apk" "$buildDir\unsigned.apk"
Assert-Success
& "$buildTools\apksigner.bat" verify "$assetDir\wpy-input.apk"
Assert-Success
Get-FileHash "$assetDir\wpy-input.apk" -Algorithm SHA256
