import sys
import json
import logging
import subprocess
import requests
import threading
from flask import Response
from time import sleep
from queue import Queue, Full


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)


q = Queue(maxsize=100)



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


class RemoteStreamer():
    consumer_count = 0
    consumer_queues = []
    url = None
    thread = None

    chunk_size = 256*1024  # 256KB
    queue_size = 50


    def __init__(self, url):
        log.info(f"RemoteStreamer({url}): Consumers: {RemoteStreamer.consumer_count}, URL: {RemoteStreamer.url}")
        RemoteStreamer.consumer_count += 1
        self.q = Queue(maxsize=self.queue_size)

        if RemoteStreamer.consumer_count == 1:
            log.info(f"Launching retriever thread for {url}")
            RemoteStreamer.url = url
            RemoteStreamer.thread = threading.Thread(target=self._retrieve_remote, daemon=True, name=f"Fetcher-{url[-20:]}")
            RemoteStreamer.thread.start()
        else:
            log.info(f"Consuming from existging thread for {RemoteStreamer.url}")

        # We do that last thing to avoid diff in delays
        RemoteStreamer.consumer_queues.append(self.q)


    def stream(self):
        # Wait until the queue is at least half full before starting to stream, to avoid too much buffering on the client side
        buffer_attempts = 30
        while(self.q.qsize() < self.queue_size // 3) and buffer_attempts > 0:
            log.info(f"Buffering... {self.q.qsize()}/{self.queue_size} chunks in queue")
            buffer_attempts -= 1
            sleep(1)

        if buffer_attempts <= 0:
            log.warning("Buffering timeout reached, aborting!")
            RemoteStreamer.consumer_count -= 1
            log.info(f"Current consumer count: {RemoteStreamer.consumer_count}")
            if self.q in RemoteStreamer.consumer_queues:
                RemoteStreamer.consumer_queues.remove(self.q)
            return None

        log.info("Starting RemoteStreamer.stream() loop")
        chunks_streamed = 0
        my_index = RemoteStreamer.consumer_count
        try:
            while True:
                if self.q in RemoteStreamer.consumer_queues:
                    chunk = self.q.get()
                    if not chunk:
                        log.info("No chunk received, exiting stream loop")
                        break
                    chunks_streamed += 1
                    if chunks_streamed % 100 == 0:
                        log.info(
                            f"Streamed {chunks_streamed} chunks ({chunks_streamed * self.chunk_size / 1024 / 1024:.2f} MB) | "
                            f"Consumers index: {my_index}"
                        )
                    yield chunk
                else:
                    log.warning("Queue not longer on consumer list, exiting")
                    break
        finally:
            log.info("Stopping RemoteStreamer.stream() loop")
            RemoteStreamer.consumer_count -= 1
            log.info(f"Current consumer count: {RemoteStreamer.consumer_count}")
            if self.q in RemoteStreamer.consumer_queues:
                RemoteStreamer.consumer_queues.remove(self.q)


    def _retrieve_remote(self):
        chunks_fetched = 0

        while RemoteStreamer.consumer_count > 0:
            log.info(f"Main retriever loop started. Consumer count: {RemoteStreamer.consumer_count}")
            try:
                r = requests.get(RemoteStreamer.url, stream=True)
                for chunk in r.iter_content(chunk_size=self.chunk_size):
                    if chunk:
                        chunks_fetched += 1
                        if chunks_fetched % 100 == 0:
                            log.info(
                                f"Fetched {chunks_fetched} chunks ({chunks_fetched * self.chunk_size / 1024 / 1024:.2f} MB) from {RemoteStreamer.url} | "
                                f"Consumers: {RemoteStreamer.consumer_count}, Queues: {len(RemoteStreamer.consumer_queues)}"
                            )
                        for q in RemoteStreamer.consumer_queues:
                            try:
                                q.put_nowait(chunk)
                            except Full:
                                log.warning(f"Queue full, removing from list.")
                                if q in RemoteStreamer.consumer_queues:
                                    RemoteStreamer.consumer_queues.remove(q)
                    else:
                        log.error("No chunk received!")
                    
                    if RemoteStreamer.consumer_count <= 0:
                        log.warning("No consumers left!")
                        break
                log.info("Finished fetching remote stream: no more chunks returned from stream!")
            except Exception as e:
                log.info(f"Retriever thread got exception: {e}")
            finally:
                if r:
                    r.close()
                log.info("Shutting down fetcher thread")



# def stream_remote(url):
#     r = requests.get(url, stream=True)

#     def generate():
#         global streaming, consuming, q, chunk_size

#         chunks_streamed = 0
#         streaming = True
#         try:
#             for chunk in r.iter_content(chunk_size=chunk_size):
#                 if chunk:
#                     chunks_streamed += 1
#                     if chunks_streamed % 100 == 0:
#                         log.info(f"Streamed {chunks_streamed} chunks ({chunks_streamed * chunk_size / 1024 / 1024:.2f} MB) from {url}")  # noqa
#                     if consuming:
#                         try:
#                             q.put_nowait(chunk)
#                         except Full:
#                             log.warning(f"Queue full, dropping chunk for {url}")
#                     yield chunk
#         finally:
#             streaming = False
#             r.close()
#             log.info(f"Generate streaming end for: {url}")

#     def consume():
#         global streaming, consuming, q, chunk_size

#         if not streaming:
#             log.info(f"ERRPR: Consumer detected no active stream!")
#             return

#         chunks_consumed = 0
#         consuming = True
#         sleep(3)
#         try:
#             while True:
#                 chunk = q.get()
#                 chunks_consumed += 1
#                 if chunks_consumed % 100 == 0:
#                     log.info(f"Consumed {chunks_consumed} chunks ({chunks_consumed * chunk_size / 1024 / 1024:.2f} MB) from {url}")  # noqa
#                 yield chunk
#         finally:
#             log.info(f"Consume streaming end for: {url}")
#             consuming = False

#     if not streaming:
#         log.info(f"Generate streaming remote from: {url}")
#         return Response(
#             generate(),
#             content_type=r.headers.get("Content-Type", "video/mp2t"),
#             headers={
#                 "Cache-Control": "no-cache",
#                 "Transfer-Encoding": "chunked"
#             }
#         )
#     else:
#         log.info(f"Consume streaming remote from: {url}")
#         return Response(
#             consume(),
#             content_type=r.headers.get("Content-Type", "video/mp2t"),
#             headers={
#                 "Cache-Control": "no-cache",
#                 "Transfer-Encoding": "chunked"
#             }
#         )
