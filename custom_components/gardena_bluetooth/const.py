"""Constants for the Gardena Bluetooth integration."""

DOMAIN = "gardena_bluetooth"

# Product type stored in the config entry at pairing time. AquaPrecise /
# Aqua Contours devices only advertise their product TLV while in pairing
# mode, so setup must NOT depend on a live advertisement scan - after a
# restart that scan can never succeed until someone physically presses the
# pairing button. Entries created before this key existed are migrated on
# their first successful scan (see __init__.async_setup_entry).
CONF_PRODUCT_TYPE = "product_type"
