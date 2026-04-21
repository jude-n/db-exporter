"""
DB Exporter — entry point.
Starts the FastAPI server then opens a native PyWebView window.
"""
import sys
import server
import webview


class API:
    """Methods exposed to the JS frontend via window.pywebview.api"""

    def browse_folder(self):
        """Open a native folder picker and return the selected path."""
        try:
            dialog = webview.FileDialog.FOLDER
        except AttributeError:
            dialog = webview.FOLDER_DIALOG
        result = webview.windows[0].create_file_dialog(dialog)
        if result and len(result) > 0:
            return result[0]
        return None


def main():
    url = server.start(host="127.0.0.1", port=5177)
    webview.create_window(
        "DB Exporter",
        url,
        width=820,
        height=680,
        min_size=(700, 560),
        resizable=True,
        js_api=API(),
    )
    webview.start()


if __name__ == "__main__":
    main()
