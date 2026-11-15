"""Convert Office documents to Markdown locally with MarkItDown."""
from io import BytesIO

from markitdown import MarkItDown, StreamInfo

# Plugins off: only the built-in converters.
_markitdown = MarkItDown(enable_plugins=False)


def convert_local(data: bytes, extension: str) -> str:
    """Convert a document to Markdown; extension includes the dot, e.g. ".docx"."""
    result = _markitdown.convert_stream(
        BytesIO(data), stream_info=StreamInfo(extension=extension)
    )
    return result.text_content.strip()
