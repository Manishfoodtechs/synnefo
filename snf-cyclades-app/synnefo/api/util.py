# Copyright (C) 2010-2017 GRNET S.A. and individual contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import re

from base64 import urlsafe_b64encode, b64decode
from urllib import quote
from hashlib import sha256
from logging import getLogger
from random import choice
from string import digits, lowercase, uppercase

from Crypto.Cipher import AES

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.db.models import Q
import json
from django.core.cache import caches

from snf_django.lib.api import faults
from synnefo.db.models import (Flavor, VirtualMachine, VirtualMachineMetadata,
                               Network, NetworkInterface, SecurityGroup,
                               BridgePoolTable, MacPrefixPoolTable, IPAddress,
                               IPPoolTable, RescueImage)
from synnefo.userdata.models import PublicKeyPair
from synnefo.plankton.backend import PlanktonBackend

from synnefo.cyclades_settings import cyclades_services, BASE_HOST,\
    PUBLIC_STATS_CACHE_NAME, VM_PASSWORD_CACHE_NAME
from synnefo.lib.services import get_service_path
from synnefo.lib import join_urls
from synnefo.logic import policy


COMPUTE_URL = \
    join_urls(BASE_HOST,
              get_service_path(cyclades_services, "compute", version="v2.0"))
SERVERS_URL = join_urls(COMPUTE_URL, "servers/")
FLAVORS_URL = join_urls(COMPUTE_URL, "flavors/")
IMAGES_URL = join_urls(COMPUTE_URL, "images/")
PLANKTON_URL = \
    join_urls(BASE_HOST,
              get_service_path(cyclades_services, "image", version="v1.0"))
IMAGES_PLANKTON_URL = join_urls(PLANKTON_URL, "images/")

NETWORK_URL = \
    join_urls(BASE_HOST,
              get_service_path(cyclades_services, "network", version="v2.0"))
NETWORKS_URL = join_urls(NETWORK_URL, "networks/")
PORTS_URL = join_urls(NETWORK_URL, "ports/")
SUBNETS_URL = join_urls(NETWORK_URL, "subnets/")
FLOATING_IPS_URL = join_urls(NETWORK_URL, "floatingips/")

PITHOSMAP_PREFIX = "pithosmap://"

BASE64_REGEXP = re.compile(
        "^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")

log = getLogger('synnefo.api')


class feature_enabled(object):
    """
    Decorator for toggling functions that are part of a feature.
    """
    def __init__(self, feature_name):
        self.feature_name = feature_name

    def __call__(self, func):

        def decorator(*args, **kwargs):
            feature_flag = getattr(settings, "%s_ENABLED"
                                   % self.feature_name.upper(), False)
            if feature_flag:
                return func(*args, **kwargs)
            else:
                raise faults.FeatureNotEnabled()
        return decorator


def random_password():
    """Generates a random password

    We generate a windows compliant password: it must contain at least
    one character from each of the groups: upper case, lower case, digits.
    """

    pool = lowercase + uppercase + digits
    lowerset = set(lowercase)
    upperset = set(uppercase)
    digitset = set(digits)
    length = 10

    password = ''.join(choice(pool) for i in range(length - 2))

    # Make sure the password is compliant
    chars = set(password)
    if not chars & lowerset:
        password += choice(lowercase)
    if not chars & upperset:
        password += choice(uppercase)
    if not chars & digitset:
        password += choice(digits)

    # Pad if necessary to reach required length
    password += ''.join(choice(pool) for i in range(length - len(password)))

    return password


def zeropad(s):
    """Add zeros at the end of a string in order to make its length
       a multiple of 16."""

    npad = 16 - len(s) % 16
    return s + '\x00' * npad


def stats_encrypt(plaintext):
    # Make sure key is 32 bytes long
    key = sha256(settings.CYCLADES_STATS_SECRET_KEY).digest()

    aes = AES.new(key)
    enc = aes.encrypt(zeropad(plaintext))
    return quote(urlsafe_b64encode(enc))


def get_random_helper_vm(for_update=False, prefetch_related=None):
    """Find a random helper VirtualMachine instance.

    This function will fetch a random helper vm that resides in an online,
    undrained Ganeti backend. For security reasons, we require the status of
    the VM to be "STOPPED".
    """
    try:
        servers = VirtualMachine.objects
        if for_update:
            servers = servers.select_for_update()
        if prefetch_related is not None:
            if isinstance(prefetch_related, list):
                servers = servers.prefetch_related(*prefetch_related)
            else:
                servers = servers.prefetch_related(prefetch_related)

        vms = servers.filter(helper=True, backend__offline=False,
                             backend__drained=False,
                             operstate="STOPPED")
        return choice(vms)
    except IndexError, VirtualMachine.DoesNotExist:
        raise faults.ItemNotFound('Helper server not found.')


def get_vm(server_id, credentials, for_update=False,
           non_deleted=False, non_suspended=False, prefetch_related=None):
    """Find a VirtualMachine instance based on ID and owner."""

    try:
        server_id = int(server_id)

        servers = policy.VMPolicy.filter_list(credentials)

        if for_update:
            servers = servers.select_for_update()
        if prefetch_related is not None:
            if isinstance(prefetch_related, list):
                servers = servers.prefetch_related(*prefetch_related)
            else:
                servers = servers.prefetch_related(prefetch_related)

        vm = servers.get(id=server_id)

        if non_deleted and vm.deleted:
            raise faults.BadRequest("Server has been deleted.")
        if non_suspended and vm.suspended:
            raise faults.Forbidden("Administratively Suspended VM")
        return vm
    except (ValueError, TypeError):
        raise faults.BadRequest('Invalid server ID.')
    except VirtualMachine.DoesNotExist:
        raise faults.ItemNotFound('Server not found.')


def get_vm_meta(vm, key):
    """Return a VirtualMachineMetadata instance or raise ItemNotFound."""

    try:
        return VirtualMachineMetadata.objects.get(meta_key=key, vm=vm)
    except VirtualMachineMetadata.DoesNotExist:
        raise faults.ItemNotFound('Metadata key not found.')


def get_image(image_id, user_id):
    """Return an Image instance or raise ItemNotFound."""

    with PlanktonBackend(user_id) as backend:
        try:
            return backend.get_image(image_id)
        except faults.ItemNotFound:
            raise faults.ItemNotFound("Image '%s' not found" % image_id)


def get_keypair(keypair_name, user_id, for_update=False):
    try:
        keypairs = PublicKeyPair.objects
        if for_update:
            keypairs = keypairs.select_for_update()
        keypair = keypairs.get(name=keypair_name, user=user_id)
        if keypair.deleted:
            raise faults.BadRequest("Keypair has been deleted.")
        return keypair
    except PublicKeyPair.DoesNotExist:
        raise faults.ItemNotFound('Keypair %s not found.' % keypair_name)


def get_image_dict(image_id, user_id):
    image = {}
    img = get_image(image_id, user_id)
    image["id"] = img["id"]
    image["name"] = img["name"]
    image["location"] = img["location"]
    image["is_snapshot"] = img["is_snapshot"]
    image["is_public"] = img["is_public"]
    image["status"] = img["status"]
    image["owner"] = img["owner"]
    image["format"] = img["disk_format"]
    image["version"] = img["version"]

    size = image["size"] = img["size"]
    mapfile = image["mapfile"] = img["mapfile"]
    image["pithosmap"] = PITHOSMAP_PREFIX + "/".join([mapfile, str(size)])

    properties = img.get("properties", {})
    image["metadata"] = dict((key.upper(), val)
                             for key, val in properties.items())

    return image


def get_rescue_image(properties=None, image_id=None):
    """
    Return a rescue image based on either a rescue image ID or
    VM specific properties.

    If properties are provided, the function will select the image based on the
    importance of each property. For example, a VM has properties
    OS-Family=Linux and OS=Debian, the system will attempt to find a Linux
    Debian rescue image, if it fails to do so, it will attempt to select a
    Linux image etc. If no image is suiting for the provided properties, a
    default image will be used.
    """
    if image_id is not None:
        try:
            return RescueImage.objects.get(id=image_id)
        except RescueImage.DoesNotExist:
            raise faults.ItemNotFound('Rescue image %d not found' % image_id)

    if properties is None:
        try:
            return RescueImage.objects.get(is_default=True, deleted=False)
        except RescueImage.DoesNotExist:
            raise faults.ItemNotFound('Rescue image not found')

    os_family = properties.os_family
    os = properties.os

    candidate_images = RescueImage.objects.filter(deleted=False)
    # Attempt to find an image that satisfies all properties
    if os_family is not None and os is not None:
        rescue_image = candidate_images.filter(
                target_os_family__iexact=os_family,
                target_os__iexact=os).first()
        if rescue_image is not None:
            return rescue_image

    # In case none are found, we should select based on the OS Family
    if os_family is not None:
        rescue_image = candidate_images.filter(
                target_os_family__iexact=os_family).first()
        if rescue_image is not None:
            return rescue_image
    try:
        # If we didn't find any images matching the criteria, fallback to
        # a default image
        return RescueImage.objects.get(is_default=True)
    except RescueImage.DoesNotExist:
        raise faults.ItemNotFound('Rescue image with properties: OS-Family %s '
                                  ' and OS %s not found' % (os_family, os))


def get_vms_using_rescue_image(rescue_image):
    """
    Return a list with the VMs that are using a specific rescue image

    This function will return a list of VirtualMachine models that are either
    in rescue mode with the underlying `rescue_image` or have a pending rescue
    request with that `rescue_image`.
    """
    if rescue_image is None:
        return []

    return VirtualMachine.objects.filter(rescue_image=rescue_image).filter(
                                         deleted=False).filter(
                                         (Q(rescue=True)) |
                                         (Q(rescue=False) &
                                          Q(action="RESCUE")))


def get_flavor(flavor_id, credentials, include_deleted=False, for_project=None,
               include_for_user=False):
    """Return a Flavor instance or raise ItemNotFound."""

    try:
        flavor_id = int(flavor_id)
        flavors = policy.FlavorPolicy\
            .filter_list(credentials, include_for_user=include_for_user)\
            .select_related("volume_type")
        if not include_deleted:
            flavors = flavors.filter(deleted=False)

        flavor = flavors.get(id=flavor_id)
        if not policy.FlavorPolicy\
           .has_access_to_flavor(flavor, credentials,
                                 project=for_project,
                                 include_for_user=include_for_user):
            raise faults.Forbidden("Insufficient access")
        return flavor
    except (ValueError, TypeError):
        raise faults.BadRequest("Invalid flavor ID '%s'" % flavor_id)
    except Flavor.DoesNotExist:
        raise faults.ItemNotFound('Flavor not found.')


def get_network(network_id, credentials, for_update=False,
                non_deleted=False):
    """Return a Network instance or raise ItemNotFound."""

    try:
        network_id = int(network_id)

        objects = policy.NetworkPolicy.filter_list(credentials)
        if for_update:
            objects = objects.select_for_update()

        network = objects.get(id=network_id)

        if non_deleted and network.deleted:
            raise faults.BadRequest("Network has been deleted.")
        return network
    except (ValueError, TypeError):
        raise faults.BadRequest("Invalid network ID '%s'" % network_id)
    except Network.DoesNotExist:
        raise faults.ItemNotFound('Network %s not found.' % network_id)


def get_port(port_id, credentials, for_update=False):
    """
    Return a NetworkInteface instance or raise ItemNotFound.
    """
    try:
        objects = policy.NetworkInterfacePolicy.filter_list(credentials)
        # if (port.device_owner != "vm") and for_update:
        #     raise faults.BadRequest('Cannot update non vm port')
        port = objects.get(id=port_id)
        if for_update:
            port = NetworkInterface.objects.select_for_update().get(id=port_id)
        return port
    except (ValueError, TypeError):
        raise faults.BadRequest("Invalid port ID '%s'" % port_id)
    except NetworkInterface.DoesNotExist:
        raise faults.ItemNotFound("Port '%s' not found." % port_id)


def get_security_group(sg_id):
    try:
        sg = SecurityGroup.objects.get(id=sg_id)
        return sg
    except (ValueError, SecurityGroup.DoesNotExist):
        raise faults.ItemNotFound("Not valid security group")


def get_floating_ip_by_address(credentials, address, for_update=False):
    try:
        objects = policy.IPAddressPolicy.filter_list(
            credentials).filter(floating_ip=True, deleted=False)
        if for_update:
            objects = objects.select_for_update()

        return objects.get(address=address)
    except IPAddress.DoesNotExist:
        raise faults.ItemNotFound("Floating IP does not exist.")


def get_floating_ip_by_id(credentials, floating_ip_id, for_update=False):
    try:
        floating_ip_id = int(floating_ip_id)

        objects = policy.IPAddressPolicy.filter_list(credentials)\
                                        .filter(floating_ip=True,
                                                deleted=False)
        if for_update:
            objects = objects.select_for_update()

        return objects.get(id=floating_ip_id)
    except IPAddress.DoesNotExist:
        raise faults.ItemNotFound("Floating IP with ID %s does not exist." %
                                  floating_ip_id)
    except (ValueError, TypeError):
        raise faults.BadRequest("Invalid Floating IP ID %s" % floating_ip_id)


def backend_has_free_public_ip(backend):
    """Check if a backend has a free public IPv4 address."""
    ip_pool_rows = IPPoolTable.objects.select_for_update()\
        .filter(subnet__network__public=True)\
        .filter(subnet__network__drained=False)\
        .filter(subnet__deleted=False)\
        .filter(subnet__network__backend_networks__backend=backend)
    for pool_row in ip_pool_rows:
        pool = pool_row.pool
        if pool.empty():
            continue
        else:
            return True


def backend_public_networks(backend):
    return Network.objects.filter(deleted=False, public=True,
                                  backend_networks__backend=backend)


def get_vm_nic(vm, nic_id):
    """Get a VMs NIC by its ID."""
    try:
        nic_id = int(nic_id)
        return vm.nics.get(id=nic_id)
    except NetworkInterface.DoesNotExist:
        raise faults.ItemNotFound("NIC '%s' not found" % nic_id)
    except (ValueError, TypeError):
        raise faults.BadRequest("Invalid NIC ID '%s'" % nic_id)


def get_nic(nic_id):
    try:
        return NetworkInterface.objects.get(id=nic_id)
    except NetworkInterface.DoesNotExist:
        raise faults.ItemNotFound("NIC '%s' not found" % nic_id)


def render_metadata(request, metadata, use_values=False, status=200):
    if request.serialization == 'xml':
        data = render_to_string('metadata.xml', {'metadata': metadata})
    else:
        if use_values:
            d = {'metadata': {'values': metadata}}
        else:
            d = {'metadata': metadata}
        data = json.dumps(d)
    return HttpResponse(data, status=status)


def render_meta(request, meta, status=200):
    if request.serialization == 'xml':
        key, val = meta.items()[0]
        data = render_to_string('meta.xml', dict(key=key, val=val))
    else:
        data = json.dumps(dict(meta=meta))
    return HttpResponse(data, status=status)


def verify_personality(personality):
    """Verify that a a list of personalities is well formed"""
    if len(personality) > settings.MAX_PERSONALITY:
        raise faults.OverLimit("Maximum number of personalities"
                               " exceeded")
    for p in personality:
        # Verify that personalities are well-formed
        try:
            assert isinstance(p, dict)
            keys = set(p.keys())
            allowed = set(['contents', 'group', 'mode', 'owner', 'path'])
            assert keys.issubset(allowed)
            contents = p['contents']
            if len(contents) > settings.MAX_PERSONALITY_SIZE:
                # No need to decode if contents already exceed limit
                raise faults.OverLimit("Maximum size of personality exceeded")
            if len(b64decode(contents)) > settings.MAX_PERSONALITY_SIZE:
                raise faults.OverLimit("Maximum size of personality exceeded")
        except (AssertionError, TypeError):
            raise faults.BadRequest("Malformed personality in request")


def verify_user_data(user_data):
    """Verify that the user_data value is valid base64 encoded value"""

    if BASE64_REGEXP.match(user_data):
        return

    raise faults.BadRequest("Marformed user_data request")


def values_from_flavor(flavor):
    """Get Ganeti connectivity info from flavor type.

    If link or mac_prefix equals to "pool", then the resources
    are allocated from the corresponding Pools.

    """
    try:
        flavor = Network.FLAVORS[flavor]
    except KeyError:
        raise faults.BadRequest("Unknown network flavor")

    mode = flavor.get("mode")

    link = flavor.get("link")
    if link == "pool":
        link = allocate_resource("bridge")

    mac_prefix = flavor.get("mac_prefix")
    if mac_prefix == "pool":
        mac_prefix = allocate_resource("mac_prefix")

    tags = flavor.get("tags")

    return mode, link, mac_prefix, tags


def allocate_resource(res_type):
    table = get_pool_table(res_type)
    pool = table.get_pool()
    value = pool.get()
    pool.save()
    return value


def release_resource(res_type, value):
    table = get_pool_table(res_type)
    pool = table.get_pool()
    pool.put(value)
    pool.save()


def get_pool_table(res_type):
    if res_type == "bridge":
        return BridgePoolTable
    elif res_type == "mac_prefix":
        return MacPrefixPoolTable
    else:
        raise Exception("Unknown resource type")


def get_existing_users():
    """
    Retrieve user ids stored in cyclades user agnostic models.
    """
    # also check PublicKeys a user with no servers/networks exist
    from synnefo.userdata.models import PublicKeyPair
    from synnefo.db.models import VirtualMachine, Network

    keypairusernames = PublicKeyPair.objects.filter().values_list('user',
                                                                  flat=True)
    serverusernames = VirtualMachine.objects.filter().values_list('userid',
                                                                  flat=True)
    networkusernames = Network.objects.filter().values_list('userid',
                                                            flat=True)

    return set(list(keypairusernames) + list(serverusernames) +
               list(networkusernames))


def vm_to_links(vm_id):
    href = join_urls(SERVERS_URL, str(vm_id))
    return [{"rel": rel, "href": href} for rel in ("self", "bookmark")]


def network_to_links(network_id):
    href = join_urls(NETWORKS_URL, str(network_id))
    return [{"rel": rel, "href": href} for rel in ("self", "bookmark")]


def subnet_to_links(subnet_id):
    href = join_urls(SUBNETS_URL, str(subnet_id))
    return [{"rel": rel, "href": href} for rel in ("self", "bookmark")]


def port_to_links(port_id):
    href = join_urls(PORTS_URL, str(port_id))
    return [{"rel": rel, "href": href} for rel in ("self", "bookmark")]


def flavor_to_links(flavor_id):
    href = join_urls(FLAVORS_URL, str(flavor_id))
    return [{"rel": rel, "href": href} for rel in ("self", "bookmark")]


def image_to_links(image_id):
    href = join_urls(IMAGES_URL, str(image_id))
    links = [{"rel": rel, "href": href} for rel in ("self", "bookmark")]
    links.append({"rel": "alternate",
                  "href": join_urls(IMAGES_PLANKTON_URL, str(image_id))})
    return links


def start_action(vm, action, jobId):
    vm.action = action
    vm.backendjobid = jobId
    vm.backendopcode = None
    vm.backendjobstatus = None
    vm.backendlogmsg = None
    vm.save()


STATS_CACHE_VALUES = {
    'spawned_servers':
    lambda: VirtualMachine.objects.exclude(operstate="ERROR").count(),
    'active_servers':
    lambda:
    VirtualMachine.objects.exclude(operstate__in=["DELETED", "ERROR"]).count(),
    'spawned_networks':
    lambda: Network.objects.exclude(state__in=["ERROR", "PENDING"]).count(),
}


def get_or_set_cache(cache, key, func):
    value = cache.get(key)
    if value is None:
        value = func()
        cache.set(key, value)
    return value


public_stats_cache = caches[PUBLIC_STATS_CACHE_NAME]


def get_cached_public_stats():
    results = {}
    for key, func in STATS_CACHE_VALUES.iteritems():
        results[key] = get_or_set_cache(public_stats_cache, key, func)
    return results


VM_PASSWORD_CACHE = caches[VM_PASSWORD_CACHE_NAME]


def can_create_flavor(flavor, user):
    policy = getattr(settings, 'CYCLADES_FLAVOR_OVERRIDE_ALLOW_CREATE', {})
    if not policy or flavor.allow_create:
        return flavor.allow_create

    groups = map(lambda g: g['name'], user['access']['user'].get('roles', []))
    policy_groups = policy.keys()
    common = set(policy_groups).intersection(groups)
    for group in common:
        allowed_flavors = policy[group]
        for flv in allowed_flavors:
            if re.compile(flv).match(flavor.name):
                return True
    return False
