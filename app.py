from flask import Flask, request, jsonify, Response, send_from_directory
import os
import re
import json
import sys


CONFIG = {}
app = Flask(__name__)


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


@app.route("/<username>/<password>/<int:stream_id>")
@app.route("/live/<username>/<password>/<int:stream_id>.ts")
@app.route("/movie/<username>/<password>/<int:stream_id>.<extension>")
@app.route("/movie/<username>/<password>/<int:stream_id>")
def proxy_stream(username, password, stream_id=None, extension=None):
    if not check_login(username, password):
        return "Unauthorized", 401

    redirect_url = None

    if request.path.startswith("/movie/"):
        for movie in CONFIG['movie_streams']:
            if movie['stream_id'] == stream_id:
                redirect_url = movie['direct_source']
                break

    if (
        request.path.startswith("/live/") or
        request.path.startswith(f"/{username}/")
    ):
        for live in CONFIG['live_streams']:
            if live['stream_id'] == stream_id:
                redirect_url = live['direct_source']
                break

    if not redirect_url:
        return "Stream not found", 404

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
        print("No existing stream data file found: ", json_data_file)
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
        print("Error: config.json is empty or invalid!")
        exit(1)

    if CONFIG["credentials"] == [] or any(
        cred["username"] == "" or cred["password"] == ""
        for cred in CONFIG["credentials"]
    ):
        print("Error: No credentials set in config.json!")
        exit(1)

    load_stream_data()

    port = \
        int(CONFIG['base_url'].split(":")[-1]) \
        if ":" in CONFIG['base_url'] else 8080
    app.run(host="0.0.0.0", port=port, debug=False)
