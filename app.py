import boto3
from flask import Flask, request, jsonify, Response, send_from_directory
from time import time
import os
import re
import json
import sys
import subprocess
import logging
import requests


CONFIG = {}
s3 = None
s3_presigneds = {}
app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

log = logging.getLogger(__name__)


def check_login(user, pwd):
    for cred in CONFIG.get("credentials", []):
        if user == cred["username"] and pwd == cred["password"]:
            return True
    return False


@app.route("/player_api.php")
def player_api():
    username = request.args.get("username")
    password = request.args.get("password")
    action = request.args.get("action")
    vod_id = int(request.args.get("vod_id", 0))

    if not check_login(username, password):
        return jsonify({"user_info": {"auth": 0}})

    if action is None:
        host = CONFIG['base_url'].split('http://')[1].split(':')[0]
        port = CONFIG['base_url'].split(":")[-1]
        return jsonify({
            "user_info": {
                "username": username,
                "password": password,
                "is_trial": 0,
                "auth": 1,
                "status": "Active"
            },
            "server_info": {
                "url": host,
                "port": port,
                "server_protocol": "http",
                "timezone": "Europe/Madrid",
            }

            # "available_channels": CONFIG['live_streams'],
            # "available_categories": CONFIG['live_categories'],

            # "movie_data": CONFIG['movie_streams'],
            # "movie_categories": CONFIG['movie_categories'],
        })

    if action == "get_live_categories":
        return jsonify(CONFIG['live_categories'])

    if action == "get_live_streams":
        return jsonify(CONFIG['live_streams'])

    if action == "get_vod_categories":
        return jsonify(CONFIG['movie_categories'])

    if action == "get_vod_streams":
        return jsonify(CONFIG['movie_streams'])

    if action == "get_series_categories":
        return jsonify(CONFIG['series_categories'])

    if action == "get_series":
        return jsonify(CONFIG['series_streams'])

    if action == "get_vod_info":
        vod = next(
            (m for m in CONFIG['movie_streams'] if m["stream_id"] == vod_id),
            None
        )
        if not vod:
            return jsonify({"error": "vod not found"})

        if vod.get("s3_hashed_name"):
            set_or_update_presigned_url(vod)

        return jsonify({
            "info": {
                "name": vod["name"],
                "movie_id": vod_id,
                "stream_type": "movie",
                "director": "",
                "cast": "",
                "rating": "",
                "description": "",
                "genre": vod.get("category_id", ""),
                "cover": vod.get("stream_icon", "")
            },
            "movie_data": {
                "stream_id": vod_id,
                "name": vod["name"],
                "container_extension": vod.get("container_extension", "mp4"),
                "stream_source": [vod["direct_source"]],
                "custom_sid": "",
                "direct_source": vod["direct_source"],
                "stream_link": vod["direct_source"]
            }
        })

    return jsonify({"error": "Unknown action"}), 400


def set_or_update_presigned_url(vod):
    key = vod["s3_hashed_name"]
    if not key:
        log.info(
            f"No s3_hashed_name for movie {vod['name']} with id "
            f"{vod['stream_id']}, skipping presigned URL generation."
        )
        return

    direct_source = None
    if key in s3_presigneds:
        expires = s3_presigneds[key]['expires']
        if expires - int(time()) > 3600:  # 1h remaining
            direct_source = s3_presigneds[key]['url']
            log.info(f"Using cached presigned URL for movie {vod['name']}")

    if direct_source:
        vod["direct_source"] = direct_source
        return

    direct_source = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": CONFIG.get('s3_uploads', {}).get('aws', {}).get('s3_bucket'),  # noqa
            "Key": key
        },
        ExpiresIn=3600*6  # in seconds
    )
    s3_presigneds[key] = {
        "url": direct_source,
        "expires": int(time()) + 3600*6
    }
    log.info(f"Generated new presigned URL for movie {vod['name']}")
    vod["direct_source"] = direct_source
    return


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
        log.info(f"Audio codec: {audio_codec}")
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


def stream_remote(url):
    r = requests.get(url, stream=True)

    log.info(f"Streaming remote from: {url}")

    def generate():
        chunks_streamed = 0
        chunk_size = 256*1024  # 256KB
        try:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if chunk:
                    chunks_streamed += 1
                    if chunks_streamed % 40 == 0:
                        log.info(f"Streamed {chunks_streamed} chunks ({chunks_streamed * chunk_size / 1024 / 1024:.2f} MB) from {url}")  # noqa
                    yield chunk
        finally:
            r.close()

    return Response(
        generate(),
        content_type=r.headers.get("Content-Type", "video/mp2t"),
        headers={
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked"
        }
    )


@app.route("/movie/<username>/<password>/<int:stream_id>.<extension>")
@app.route("/movie/<username>/<password>/<int:stream_id>")
def proxy_movie(username, password, stream_id=None, extension=None):
    if not check_login(username, password):
        return "Unauthorized", 401

    log.info(f"Requested movie: {stream_id}, extension: {extension}")

    redirect_url = None
    audio_codec = None
    ua_header = request.headers.get("User-Agent", "").lower()
    range_header = request.headers.get("Range")
    log.info(f"MOVIE. User agent: {ua_header}, Range: {range_header}")

    for movie in CONFIG['movie_streams']:
        if movie['stream_id'] == stream_id:
            if movie.get("s3_hashed_name"):
                set_or_update_presigned_url(movie)
            redirect_url = movie['direct_source']
            if not range_header:
                audio_codec = detect_audio_codec(redirect_url)
            break

    if not redirect_url:
        return "Stream not found", 404

    transcode = (
        (not range_header) and
        audio_codec in ("eac3", ) and
        not any(x in ua_header.lower() for x in ("mozilla", "vlc"))
    )

    if transcode:
        log.info(f"Transcoding from url: {redirect_url}")
        return stream_ffmpeg(
            ffmpeg_transcode_audio(redirect_url),
            content_type="video/mp4"
        )
    else:
        return Response(
            f"Redirecting to {redirect_url}", status=302,
            headers={"Location": redirect_url}
        )


@app.route("/<username>/<password>/<int:stream_id>")
@app.route("/live/<username>/<password>/<int:stream_id>.ts")
def proxy_live(username, password, stream_id=None):
    if not check_login(username, password):
        return "Unauthorized", 401

    log.info(f"Requested live stream: {stream_id}")

    redirect_url = None
    category_id = None

    for live in CONFIG['live_streams']:
        if live['stream_id'] == stream_id:
            redirect_url = live['direct_source']
            name = live.get('name')
            category_id = live.get('category_id')
            break

    if not redirect_url:
        return "Stream not found", 404

    category_ids = [
        live.get('category_id')
        for live in CONFIG['live_streams'] if live.get('name') == name
    ]

    if any(c in CONFIG.get("proxy_categories", []) for c in category_ids if c):
        log.info(f"Proxying live stream {stream_id} in category {category_id}")
        return stream_remote(redirect_url)
    else:
        log.info(f"Sending redirect URL {redirect_url} for live stream {stream_id} in category {category_id}")
        return Response(
            f"Redirecting to {redirect_url}", status=302,
            headers={"Location": redirect_url}
        )


@app.route("/xmltv.php")
def xmltv():
    epg = '<?xml version="1.0" encoding="UTF-8"?><tv></tv>'
    return Response(epg, content_type="application/xml")


@app.route("/logos/<path:filename>")
def logos(filename):
    if not re.match(r"^[a-f0-9]{32}\.[a-z]{3,4}$", filename):
        return "400 Invalid filename", 400
    else:
        return send_from_directory("logos", filename)


def load_stream_data():
    json_data_file = CONFIG.get('json_data_file', 'final_data.json')
    if not json_data_file or not os.path.exists(json_data_file):
        log.info("No existing stream data file found: ", json_data_file)
        sys.exit(1)

    with open(json_data_file) as f:
        data = json.load(f)
        CONFIG["live_streams"] = data.get("live_streams", [])
        CONFIG["movie_streams"] = data.get("movie_streams", [])
        CONFIG["series_streams"] = data.get("series_streams", [])
        CONFIG["live_categories"] = data.get("live_categories", [])
        CONFIG["movie_categories"] = data.get("movie_categories", [])
        CONFIG["series_categories"] = data.get("series_categories", [])


if __name__ == "__main__":
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    with open(config_file) as f:
        CONFIG = json.load(f)

    if not CONFIG:
        log.info("Error: config.json is empty or invalid!")
        exit(1)

    if CONFIG["credentials"] == [] or any(
        cred["username"] == "" or cred["password"] == ""
        for cred in CONFIG["credentials"]
    ):
        log.info("Error: No credentials set in config.json!")
        exit(1)

    s3 = boto3.client(
        "s3",
        aws_access_key_id=CONFIG.get("s3_uploads", {}).get("aws", {}).get("aws_access_key_id"),  # noqa
        aws_secret_access_key=CONFIG.get("s3_uploads", {}).get("aws", {}).get("aws_secret_access_key"),  # noqa
        region_name=CONFIG.get("s3_uploads", {}).get("aws", {}).get("region_name"),  # noqa
    )

    load_stream_data()

    port = \
        int(CONFIG['base_url'].split(":")[-1]) \
        if ":" in CONFIG['base_url'] else 8080
    app.run(host="0.0.0.0", port=port, debug=False)
