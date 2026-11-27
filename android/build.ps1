param(
    [string]$Sdk = $env:ANDROID_HOME,
    [string]$Jdk = $env:JAVA_HOME,
    [string]$SigningDirectory = (Join-Path $env:LOCALAPPDATA 'JobPilotSigning')
)
$ErrorActionPreference = 'Stop'
if (!$Sdk -or !$Jdk) { throw 'Set ANDROID_HOME and JAVA_HOME first.' }
$toolsDir = Join-Path $Sdk 'build-tools/35.0.0'
$platform = Join-Path $Sdk 'platforms/android-35/android.jar'
$env:JAVA_HOME = $Jdk
$env:PATH = "$Jdk\bin;$env:PATH"
function Run([string]$file, [string[]]$arguments) {
    & $file @arguments
    if ($LASTEXITCODE -ne 0) { throw "Build command failed: $([IO.Path]::GetFileName($file)) ($LASTEXITCODE)" }
}
$work = Join-Path ([IO.Path]::GetTempPath()) ("jobpilot-apk-" + [guid]::NewGuid())
$generated = Join-Path $work 'gen'
$classes = Join-Path $work 'classes'
$dex = Join-Path $work 'dex'
$out = Join-Path $PSScriptRoot 'dist'
New-Item -ItemType Directory -Force $work,$generated,$classes,$dex,$out | Out-Null
$dest = Join-Path $out 'jobpilot-assistant.apk'
$stage = Join-Path $work 'resources.apk'
Copy-Item -LiteralPath "$PSScriptRoot/res" -Destination "$work/res" -Recurse
Copy-Item -LiteralPath "$PSScriptRoot/AndroidManifest.xml" -Destination "$work/AndroidManifest.xml"
Run "$toolsDir/aapt2.exe" @('compile','--dir',"$work/res",'-o',"$work/resources.zip")
Run "$toolsDir/aapt2.exe" @('link','-o',$stage,'-I',$platform,'--manifest',"$work/AndroidManifest.xml",'--java',$generated,"$work/resources.zip")
$sources = @(Get-ChildItem "$PSScriptRoot/src",$generated -Filter '*.java' -Recurse | ForEach-Object { $_.FullName })
Run "$Jdk/bin/javac.exe" (@('-encoding','UTF-8','--release','8','-classpath',$platform,'-d',$classes) + $sources)
Run "$Jdk/bin/jar.exe" @('cf',"$work/classes.jar",'-C',$classes,'.')
Run "$toolsDir/d8.bat" @('--release','--min-api','26','--lib',$platform,'--output',$dex,"$work/classes.jar")
# ZIP/JAR update writes only the DEX entry; aapt2's binary manifest and resources remain intact.
Run "$Jdk/bin/jar.exe" @('uf',$stage,'-C',$dex,'classes.dex')
Run "$toolsDir/zipalign.exe" @('-f','4',$stage,"$work/aligned.apk")

$store = Join-Path $SigningDirectory 'assistant.p12'
$wrapped = Join-Path $SigningDirectory 'password.dpapi'
New-Item -ItemType Directory -Force $SigningDirectory | Out-Null
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Run 'icacls.exe' @($SigningDirectory,'/inheritance:r','/grant:r',"*$($userSid):(OI)(CI)F",'*S-1-5-18:(OI)(CI)F')
if ((Test-Path $store) -ne (Test-Path $wrapped)) { throw 'Signing store incomplete. Restore the original signing key before updating.' }
try {
    if (!(Test-Path $store)) {
        $random = New-Object byte[] 36
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($random) } finally { $rng.Dispose() }
        $env:JOBPILOT_APK_STORE_PASS = [Convert]::ToBase64String($random)
        $secret = ConvertTo-SecureString $env:JOBPILOT_APK_STORE_PASS -AsPlainText -Force
        ConvertFrom-SecureString $secret | Set-Content -LiteralPath $wrapped -Encoding ascii
        Run "$Jdk/bin/keytool.exe" @('-genkeypair','-storetype','PKCS12','-keystore',$store,
            '-storepass:env','JOBPILOT_APK_STORE_PASS','-keypass:env','JOBPILOT_APK_STORE_PASS','-alias','jobpilot',
            '-keyalg','RSA','-keysize','3072','-validity','10000','-dname','CN=JobPilot Personal Android')
    } else {
        $secret = ConvertTo-SecureString (Get-Content -LiteralPath $wrapped -Raw).Trim()
        $env:JOBPILOT_APK_STORE_PASS = (New-Object PSCredential 'jobpilot',$secret).GetNetworkCredential().Password
    }
    Run "$toolsDir/apksigner.bat" @('sign','--ks',$store,'--ks-key-alias','jobpilot','--ks-pass','env:JOBPILOT_APK_STORE_PASS',
        '--v4-signing-enabled','false','--out',"$work/signed.apk","$work/aligned.apk")
    Run "$toolsDir/apksigner.bat" @('verify','--verbose','--print-certs',"$work/signed.apk")
    Copy-Item -LiteralPath "$work/signed.apk" -Destination $dest -Force
    Get-FileHash -Algorithm SHA256 $dest
} finally {
    Remove-Item Env:\JOBPILOT_APK_STORE_PASS -ErrorAction SilentlyContinue
}
