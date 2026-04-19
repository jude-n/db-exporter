"""
DB Exporter — entry point.
Starts the FastAPI server then opens a native PyWebView window.
"""
import sys
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
    webview.start()


if __name__ == "__main__":
    main()
