#!/usr/bin/python3
import argparse
import json
import digitalocean
import os
import tempfile
import logging
import upnpclient


IGD = {"urn:schemas-upnp-org:device:InternetGatewayDevice:1"}

IGD_DEVICE_FILE = "/dev/shm/igd_device"
IP_FILE = "/dev/shm/external_ip"


logger = logging.getLogger(__name__)


def load_igd(filename):
    if not os.path.exists(filename):
        return
    with open(filename, "r") as f:
        try:
            igd_device = json.load(f)
            device_name = igd_device.get("device_name")
            if not device_name:
                return
            logger.debug("Using URL %s", device_name)
            igd = upnpclient.Device(device_name)
            return igd
        except Exception:
            logger.exception("Failed to read %s", filename)


def save_igd(filename, device):
    write_file(filename, json.dumps({"device_name": device.device_name}))


def discover_igd():
    devices = upnpclient.discover()
    igd_devices = list(filter(lambda d: d.device_type in IGD, devices))
    logger.debug("IGD devices: %s", igd_devices)
    if len(igd_devices) != 1:
        raise SystemExit()
    igd = igd_devices.pop()

    return igd


def get_wan_service(device, connection_service=None):
    if not connection_service:
        dcs = device.Layer3Forwarding1.GetDefaultConnectionService()
        if not dcs or "NewDefaultConnectionService" not in dcs:
            raise SystemExit()
        connection_service = dcs["NewDefaultConnectionService"].split(":")[-1]
    wan_service = device.service_map.get(connection_service)
    if not wan_service:
        raise SystemExit()
    logger.debug("WAN service: %s", wan_service)
    return wan_service


def get_record(domain_name, record_name, token):
    manager = digitalocean.Manager(token=token)
    dns_domain = manager.get_domain(domain_name)
    dns_records = list(
        filter(lambda r: r.name == record_name, dns_domain.get_records())
    )
    if len(dns_records) != 1:
        raise SystemExit()
    dns_record = dns_records.pop()
    logger.debug("DNS record: %s", dns_record)
    return dns_record


def write_file(filename, data):
    f = tempfile.NamedTemporaryFile(
        mode="w+", dir=os.path.dirname(filename), delete=False
    )
    try:
        f.write(data)
        f.close()
    except Exception:
        os.unlink(f.name)
    os.rename(f.name, filename)


def main(token, domain_name, record_name, connection_service=None):
    dns_record = get_record(domain_name, record_name, token)
    logger.info("Configured IP: %s", dns_record.data)

    igd = load_igd(IGD_DEVICE_FILE) or discover_igd()
    logger.info("Found IGD: %s", igd)
    save_igd(IGD_DEVICE_FILE, igd)
    logger.debug("File %s updated with %s", IGD_DEVICE_FILE, igd.device_name)

    wan_service = get_wan_service(igd, connection_service)
    external_ip = wan_service.GetExternalIPAddress().get("NewExternalIPAddress")
    logger.info("External IP: %s", external_ip)

    if external_ip != dns_record.data:
        dns_record.data = external_ip
        dns_record.save()
        logger.info("Record updated")

    if external_ip != dns_record.data or not os.path.exists(IP_FILE):
        write_file(IP_FILE, "{}\n".format(external_ip))
        logger.info("File %s updated with %s", IP_FILE, external_ip)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update DigitalOcean IP address.")
    parser.add_argument(
        "-d",
        "--debug",
        help="Debug output",
        dest="debug",
        action="store_true",
        default=False,
    )
    parser.add_argument("--token", help="DigitalOcean API token", dest="token")
    parser.add_argument("--domain_name", help="Domain name", dest="domain_name")
    parser.add_argument("--record_name", help="Record name", dest="record_name")
    parser.add_argument(
        "--connection_service",
        help="Override WAN service name",
        dest="connection_service",
    )

    args = parser.parse_args()
    logging.basicConfig(level=args.debug and logging.DEBUG or logging.INFO)
    if not (args.token and args.domain_name and args.record_name):
        parser.print_help()
        raise SystemExit()
    args_dict = {k: v for k, v in args.__dict__.items() if k != "debug"}
    logger.debug(
        "Arguments: %s", " ".join("--{}={}".format(k, v) for k, v in args_dict.items())
    )
    main(**args_dict)
