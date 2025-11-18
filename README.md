# xtreamer

Server for IPTV Xtream Code clients.

## Parse your backend
Having a valid IPTV Xtream Code server, run create_data.py to retrieve available media, filter according to your criteria, create missing icons, and create custom groups for easy navigation.
All these processes are configured through the config.json file.

```bash
python3 create_data.py my_config.json
```

The process will create 3 files:
- 0_upstream_data.json with raw data from your provider
- 1_filtered_data.json it's the filtered raw data, so just your selected groups
- 2_processed_data.json has direct_source url, ids reordered and remapped with all gaps filled
- 3_final_data.json has all icons fetched locally for live streams, filling missing ones


## Start your server

Ideally, add it to your systemcl system for automatic start and restart. For instance:

```bash
# This is an example file for /etc/systemd/system/xtreamer.service
[Unit]
Description=XTream Server
After=network.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/xtreamer
ExecStart=python3 app.py my_config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

For testing you can manually run:

```bash
python3 app.py my_config.json
```

## S3
utils/upload_to_s3.py allows to upload local files to S3 bucket.
Usage:
```bash
python3 utils/uplaod_to_s3.py my_config.json /path/to/movie1 /path/to/movie2
```
or:
```bash
python3 utils/uplaod_to_s3.py my_config.json /path/to/movies/*.mkv
```
Required in config file:
```json
    "aws": {
        "s3_bucket": "bucket name",
        "aws_access_key_id": "acces key",
        "aws_secret_access_key": "secret access key",
        "region_name": "eu-north-1"
    },
    "tmdb_api_key": "tmdb api key"
```
