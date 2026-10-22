"""S3 destination accepting the same typed artifacts as filesystem."""

from pathlib import Path
from uuid import uuid4

from ..artifacts import Artifact, accepted


def s3(value, settings, context):
    import io

    import boto3

    value = accepted(value, s3.input_types, owner="s3")
    client = boto3.client("s3", **settings.get("client", {}))
    key = settings.get("key", settings.get("prefix", "autostacgis").strip("/") + "/" + uuid4().hex)
    if value.type == "path":
        path = Path(value.value)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    client.upload_file(
                        str(child), settings["bucket"], key + "/" + child.relative_to(path).as_posix()
                    )
        else:
            client.upload_file(str(path), settings["bucket"], key)
    else:
        # Streaming upload with a bounded, seek-free reader.
        class Reader(io.RawIOBase):
            def __init__(self):
                self.chunks = iter(value.chunks())
                self.pending = b""

            def readable(self):
                return True

            def readinto(self, buffer):
                while not self.pending:
                    try:
                        self.pending = next(self.chunks)
                    except StopIteration:
                        return 0
                count = min(len(buffer), len(self.pending))
                buffer[:count], self.pending = self.pending[:count], self.pending[count:]
                return count

        client.upload_fileobj(io.BufferedReader(Reader()), settings["bucket"], key)
    return Artifact("path", f"s3://{settings['bucket']}/{key}", value.media_type)


s3.input_types = ["bytes", "byte-stream", "path"]
s3.output_types = ["path"]
ID = "s3"
REQUIRES = ()
IMPLEMENTATIONS = {"data_out": s3}
