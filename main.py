"""
DB Exporter — entry point.
Starts the FastAPI server then opens a native PyWebView window.

  macOS   — uses the default WebKit renderer (built into macOS, no extra deps)
  Windows — uses Edge/Chromium (WebView2) renderer, pre-installed on Win10/11,
            no admin rights or .NET install required
"""
import platform
import server
import webview


def main():
    url = server.start(host="127.0.0.1", port=5177)

    webview.create_window(
        "DB Exporter",
        url,
        width=820,
        height=680,
        min_size=(700, 560),
        resizable=True,
    )

    if platform.system() == "Windows":
        webview.start(gui="edgechromium")
    else:
        webview.start()


if __name__ == "__main__":
    main()
