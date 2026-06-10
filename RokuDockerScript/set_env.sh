#!/bin/bash
set -e

JSON_FILE="/root/roku_automation/shared_resources/pid_mappings.json"
tmpfile=$(mktemp)

# 1. Modify stress hdmi max vol
sed -i 's/MAX_VOL = 10/MAX_VOL = 1/g' /root/automated_tests/tests/roku_os/functional/audio/test_volume_hdmi_stress.py

# 2. Add pid mapping info.
# Find the line number of the root closing brace " }"
root_close_line=$(grep -n "^ }$" "$JSON_FILE" | tail -1 | cut -d: -f1)
prev_line=$((root_close_line - 1))

{
    # Print all lines up to the last entry, adding a comma to its closing brace
    head -n "$prev_line" "$JSON_FILE" | sed '$ s/}$/},/'

    # Append new entries and close the root object
    cat << 'EOF'
        "S20K": {
            "model": "H595X",
            "platform": "miami",
            "manufacturer": "Changhong",
            "brand_name": "Hiro"
        },
        "S27G": {
            "model": "P127X",
            "platform": "damon",
            "manufacturer": "TCL",
            "brand_name": "TCL"
        },
        "S2AU": {
            "model": "H311X",
            "platform": "miami",
            "manufacturer": "TPV",
            "brand_name": "Hiro"
        },
        "S20M": {
            "model": "P417X",
            "platform": "damon",
            "manufacturer": "MOKA",
            "brand_name": "Roku"
        },
        "S20N": {
            "model": "P418X",
            "platform": "damon",
            "manufacturer": "MOKA",
            "brand_name": "Roku"
        },
        "S20P": {
            "model": "P419X",
            "platform": "damon",
            "manufacturer": "MOKA",
            "brand_name": "Roku"
        },
        "S23F": {
            "model": "P826X",
            "platform": "damon",
            "manufacturer": "MTC",
            "brand_name": "JVC"
        },
        "S23D": {
            "model": "P824X",
            "platform": "damon",
            "manufacturer": "MTC",
            "brand_name": "element"
        }
 }
EOF
} > "$tmpfile" && mv "$tmpfile" "$JSON_FILE"

echo "Set env finished!"
