from setuptools import setup

APP = ["TimeCap.py"]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "TimeCap",
        "CFBundleDisplayName": "TimeCap",
        "CFBundleIdentifier": "com.haenginro.timecap",
        "CFBundleVersion": "1.2",
        "CFBundleShortVersionString": "1.2",
        "LSUIElement": True,  # menu bar app — no dock icon
    },
    "iconfile": "TimeCap.icns",
    "packages": ["rumps"],
}

setup(
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
