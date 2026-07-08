# Roomba980-Wifi
Configure irobot wifi series using command line

I finally got there!

# Steps
## Push a password
1. Reset to robot (press Clean until **all** lights up)
2. Start Soft AP (press Home+Spot) until sound + green wifi led blink - Eventually try again if the error bip sounds
1. The password is pushed to the robot using an MQTT Authentication Exchange packet
    `echo -n "f023efcc3b29003a313a313537393139353338363a386678376e597156744b67574a39744f" | xxd -r -p | openssl s_client -CAfile robot-ca.pem  -connect 192.168.10.1 -quiet -noservername`. This output the password back if set correctly.

**packet format:**

Value | Description
----- | ----------
`0xf0` | MQTT Authentication Exchange packet fixed header
`0x23` | The size of the remaining Bytes after this (35 in decimal)
`0xefcc3b2900` | Voodoo packet :smile_cat:
`:1:1579195386:8fx7nYqVtKgWJ9tO` | Format: `:1:timestamp:16 alpha-decimal chars`

Note: I have not played using different password format, so use with caution.

## Provision wifi settings
Connect using mqtt protocol v3.1.1 and send commands via `json` messages from the table bellow on the right topic and **disconnect after**. (I use only 6, 9, 10, 11, 12, it seems to be sufficient for a basic configuration). Having a little pause between commands seems to help the process a bit. Once sent, the SoftAP should turn off and the wifi led should turn fixed white.

&nbsp; | Topic | payload | Comment
------- | ------- | ---------- | --------------
1 | delta | { "state" : { "timezone" : "Europe/Paris" } } | makes the robot bip once of two
2 | wifictl | { "state" : { "sdiscUrl" : "https://disc-prod.iot.irobotapi.com/v1/robot/discover?robot_id=0000000000000000&country_code=FR&sku=R966040" } } | I did not checked what is this api…
3 | wifictl | { "state" : { "ntphosts" : "0.irobot.pool.ntp.org 1.irobot.pool.ntp.org 2.irobot.pool.ntp.org 3.irobot.pool.ntp.org" } }
4 | delta | { "state" : {"country" : "FR"} }
5 | delta | { "state" : { "cloudEnv" : "prod" } }
6 | wifictl | {"state": {"wlcfg": {"pass": "wifisecretpasssword", "sec": 7, "ssid": "575757"}}} | `pass` as clear text, `ssid` as octal (mine is `WWW` here), `sec`???
7 | wifictl | { "state" : { "utctime" : 1579291795 } } | timestamp
8 | wifictl | { "state" : { "localtimeoffset" : 60 } }
9 | wifictl | { "state" : { "chkssid" : true } }
10 | wifictl | { "state" : { "wactivate" : true } }
11 | wifictl | { "state" : { "get" : "netinfo" } }
12 | wifictl | { "state" : { "uap" : false } }

## Usage

`initial-config.py` pushes the robot password and provisions Wi-Fi. Requires [uv](https://docs.astral.sh/uv/) (or Python 3.11+ with paho-mqtt installed).

1. Reset the robot (press Clean until **all** lights flash)
2. Enter soft-AP mode (press Home+Spot until melody + blinking WiFi LED)
3. Connect your PC to the Roomba access point (SSID = BLID)

```bash
cp provision.toml.example provision.toml
# edit provision.toml — robot BLID/password and your home Wi-Fi credentials
./initial-config.py
# or: ./initial-config.py -c /path/to/provision.toml
```

`provision.toml` is gitignored. Only `provision.toml.example` belongs in the repo.

The timezone command **must** produce a beep from the robot.

```
$ ./initial-config.py
recv 2 bytes: f023
recv 35 bytes: efcc3b2900...
auth response (37 bytes): f023efcc3b2900...
publish delta {"state":{"timezone":"America/Chicago"}}    <<<<======= THIS MUST EMIT A SOUND
publish wifictl {"state":{"wlcfg":{...}}}
...
```

After a few seconds the soft-AP should drop and the WiFi LED should stay solid white.

# Thanks
I want to thanks my wife and my familly ;)

Some code in the prototype copied from https://github.com/NickWaterton/Roomba980-Python

# Links
* https://github.com/koalazak/dorita980/issues/106
* https://github.com/NickWaterton/Roomba980-Python/issues/74
