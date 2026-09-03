import sys
import json
import logging
import subprocess
import requests
import threading
from collections import deque
from flask import Response
from time import sleep


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)


# MPEG-TS packet size. Chunks are published as whole packets so a viewer that
# snaps forward to the live edge always lands on a packet boundary.
TS_PACKET = 188


def detect_audio_codec(url, timeout=5):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "json",
        url
    ]

    try:
        out = subprocess.check_output(cmd, timeout=timeout)
        data = json.loads(out)
        audio_codec = data["streams"][0]["codec_name"]
        log.info(f"Detected audio codec: {audio_codec} for: {url}")
        return audio_codec
    except Exception:
        return None


def stream_ffmpeg(cmd, content_type="video/mp4"):
    """
    Executa FFmpeg i fa streaming del stdout cap al client
    """

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=10**6
    )

    def generate():
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            process.kill()

    return Response(
        generate(),
        content_type=content_type,
        headers={
            "Cache-Control": "no-cache",
            "Accept-Ranges": "bytes"
        }
    )


def ffmpeg_transcode_audio(src_url):
    """
    Vídeo copy + àudio → AAC
    """
    return [
        "ffmpeg",
        "-loglevel", "error",

        # reconnect HLS
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",

        "-i", src_url,

        # map explícit (1 vídeo + 1 àudio)
        "-map", "0:v:0",
        "-map", "0:a:0",

        # vídeo intacte
        "-c:v", "copy",

        # 🔊 àudio compatible universal
        "-c:a", "aac",
        "-b:a", "192k",

        # mp4 fragmentat
        "-movflags", "frag_keyframe+empty_moov",
        "-f", "mp4",
        "pipe:1"
    ]


class _Session():
    """
    One upstream connection, shared by every viewer currently watching.

    Chunks land in a single ring buffer tagged with a sequence number; viewers
    hold nothing but a cursor into it. Nobody gets a private copy of the data,
    so a slow viewer can never build up a backlog of its own: it just falls
    behind in the ring and gets snapped back to the live edge.
    """

    def __init__(self, url, chunk_size, ring_len):
        self.url = url
        self.chunk_size = chunk_size
        self.buf = deque(maxlen=ring_len)   # (seq, chunk)
        self.head = 0                       # seq the next chunk will get
        self.cv = threading.Condition()
        self.viewers = 0
        self.stopped = False
        self.thread = None
        self._pending = b""

    def publish(self, chunk):
        data = self._pending + chunk if self._pending else chunk
        cut = len(data) - (len(data) % TS_PACKET)
        if not cut:
            self._pending = data
            return

        self._pending = data[cut:]
        with self.cv:
            self.buf.append((self.head, data[:cut]))
            self.head += 1
            self.cv.notify_all()

    def fetch(self):
        chunks = 0

        while not self.stopped:
            r = None
            # A new connection restarts at a packet boundary, so anything left
            # over from the previous one would misalign the stream.
            self._pending = b""
            try:
                log.info(f"Connecting to upstream {self.url}")
                r = requests.get(self.url, stream=True, timeout=(5, 15))
                for chunk in r.iter_content(chunk_size=self.chunk_size):
                    if self.stopped:
                        break
                    if not chunk:
                        continue
                    chunks += 1
                    if chunks % 500 == 0:
                        log.info(
                            f"Fetched {chunks} chunks "
                            f"({chunks * self.chunk_size / 1024 / 1024:.2f} MB) "
                            f"| Viewers: {self.viewers}"
                        )
                    self.publish(chunk)
                log.info("Upstream returned no more data")
            except Exception as e:
                log.warning(f"Fetcher error: {e}")
            finally:
                if r is not None:
                    r.close()

            if not self.stopped:
                sleep(1)  # backoff before reconnecting

        # Wake up anyone still waiting on us so they can close their response
        with self.cv:
            self.cv.notify_all()
        log.info(f"Fetcher thread for {self.url} finished")


class RemoteStreamer():
    chunk_size = 64*1024   # 64KB, a whole number of TS packets after publish()
    ring_len = 512         # ~32MB of recent stream (~40s of a 6Mbps channel)
    lead = 40              # join/resync this far behind live (~3s of cushion)
    stall_timeout = 20     # give up on a viewer if upstream is silent this long

    # Players do not read continuously: they fill their own buffer, stop
    # reading for several seconds, then take another burst. That backpressure
    # is normal and must NOT be treated as lag, because skipping forward
    # discards packets the player never received and leaves a hole in the TS.
    # The only thing that genuinely forces a skip is a backlog about to be
    # overwritten in the ring, so the threshold belongs to the ring size.
    max_lag = ring_len - 64

    _lock = threading.Lock()
    _session = None

    def __init__(self, url):
        with RemoteStreamer._lock:
            session = RemoteStreamer._session

            # Deliberate: a second viewer joins whatever is already being
            # fetched, it does not open a second upstream connection.
            if session is None or session.stopped:
                session = _Session(url, self.chunk_size, self.ring_len)
                session.thread = threading.Thread(
                    target=session.fetch, daemon=True,
                    name=f"Fetcher-{url[-20:]}"
                )
                RemoteStreamer._session = session
                session.viewers += 1
                self.viewer_id = session.viewers  # for logging only
                session.thread.start()
                log.info(f"RemoteStreamer({url}): launched new fetcher | viewer_id={self.viewer_id}")
            else:
                session.viewers += 1
                self.viewer_id = session.viewers  # for logging only
                log.info(
                    f"RemoteStreamer({url}): joining existing fetcher for "
                    f"{session.url}, now {session.viewers} viewers | viewer_id={self.viewer_id}"
                )

            self.session = session

    def _release(self):
        session = self.session
        with RemoteStreamer._lock:
            session.viewers -= 1
            remaining = session.viewers
            if remaining <= 0:
                # Detach the session before the fetcher has actually noticed:
                # the next viewer builds a fresh one instead of racing this
                # thread, and this thread exits at its next chunk or timeout.
                session.stopped = True
                if RemoteStreamer._session is session:
                    RemoteStreamer._session = None

        log.info(f"Viewer {self.viewer_id} left, {max(remaining, 0)} remaining")

    def stream(self):
        session = self.session

        with session.cv:
            cursor = max(session.head - self.lead, 0)
        log.info(f"Viewer {self.viewer_id} starting at seq {cursor}, live edge {session.head}")

        served = 0
        try:
            while True:
                with session.cv:
                    while not session.stopped and (
                        not session.buf or session.buf[-1][0] < cursor
                    ):
                        if not session.cv.wait(timeout=self.stall_timeout):
                            log.warning(f"Upstream stalled, closing viewer {self.viewer_id}")
                            return
                    if session.stopped:
                        log.info(f"Session stopped, closing viewer {self.viewer_id}")
                        return

                    oldest = session.buf[0][0]
                    newest = session.buf[-1][0]

                    # Too far behind (or fallen off the back of the ring):
                    # skip the backlog instead of delivering stale video. This
                    # is what keeps every viewer on the same picture.
                    if cursor < oldest or newest - cursor > self.max_lag:
                        dropped = max(newest - self.lead, oldest) - cursor
                        cursor = max(newest - self.lead, oldest)
                        log.warning(
                            f"Viewer {self.viewer_id} resync to seq {cursor}, live edge {newest}"
                            f" (dropped {dropped} chunks)"
                        )

                    out = [
                        chunk for seq, chunk in session.buf if seq >= cursor
                    ]
                    cursor = newest + 1

                # Socket writes happen outside the lock: a slow viewer must
                # never be able to stall the fetcher or the other viewers.
                for chunk in out:
                    served += 1
                    if served % 500 == 0:
                        log.info(
                            f"Viewer {self.viewer_id} streamed {served} chunks "
                            f"({served * self.chunk_size / 1024 / 1024:.2f} MB)"
                        )
                    yield chunk
        finally:
            log.info(f"Viewer {self.viewer_id} stopping RemoteStreamer.stream() loop")
            self._release()
