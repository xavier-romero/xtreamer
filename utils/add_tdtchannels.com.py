import sys
import json
import requests
from time import time


# our main data file
if len(sys.argv) < 2:
    print("Usage: python add_vod_files.py config.json")
    sys.exit(1)

config_file = sys.argv[1]
with open(config_file) as f:
    CONFIG = json.load(f)

json_data_file = CONFIG.get('json_data_file', 'final_data.json')
with open(json_data_file, "r") as f:
    data = json.load(f)

live_nums = [
    live_stream.get('num') for live_stream in data.get('live_streams')
]
stream_ids = [
    live_stream.get('stream_id') for live_stream in data.get('live_streams')
]

next_live_num = max(live_nums) + 1
next_stream_id = max(stream_ids) + 1

ambits_whitelist = \
    CONFIG.get("tdtchannels_com", {}).get("whitelisted_ambits", [])

# tdtchannels.com import
prefix = "tdtch"
tdtch_data = requests.get("https://www.tdtchannels.com/lists/tv.json").json()

live_categories = []
live_streams = []

for ambit in tdtch_data['countries'][0]['ambits']:
    if ambit['name'] not in ambits_whitelist:
        print(f"Skipping ambit/category: {ambit['name']}")
        continue

    category_id = f"{prefix}_{ambit['name']}".replace(' ', '_').lower()
    category_name = f"{prefix.upper()} | {ambit['name']}"

    channels_found = 0
    for channel in ambit['channels']:
        if not channel['options']:
            continue
        if not any(opt["format"] == "m3u8" for opt in channel['options']):
            continue

        direct_source = [
            opt["url"] for opt in channel['options']
            if opt["format"] == "m3u8"
        ][0]

        channel = {
            "num": next_live_num,
            "name": channel['name'],
            "stream_type": "live",
            "stream_id": next_stream_id,
            "stream_icon": channel['logo'],
            "epg_channel_id": channel['epg_id'],
            "added": int(time()),
            "category_id": category_id,
            "tv_archive": 0,
            "direct_source": direct_source
        }
        next_live_num += 1
        next_stream_id += 1
        live_streams.append(channel)
        channels_found += 1

    if channels_found > 0:
        category = {
            "category_id": category_id,
            "category_name": category_name,
        }
        live_categories.append(category)

        print(
            f"Ambit: {ambit['name']}, "
            f"Category ID: {category_id}, "
            f"Category Name: {category_name} "
            f"Channels Found: {channels_found}"
        )

data['live_categories'].extend(live_categories)
data['live_streams'].extend(live_streams)

with open(json_data_file, "w") as f:
    json.dump(data, f, indent=4)
print(f"Added {len(live_streams)} live streams to {json_data_file}.")
