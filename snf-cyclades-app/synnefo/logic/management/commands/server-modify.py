# Copyright (C) 2010-2017 GRNET S.A.
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

from optparse import make_option

from snf_django.lib.api import Credentials
from django.core.management.base import CommandError

from synnefo.management.common import (get_resource, convert_api_faults,
                                       wait_server_task)
from snf_django.management.commands import SynnefoCommand
from snf_django.management.utils import parse_bool
from synnefo.logic import servers


ACTIONS = ["start", "stop", "reboot_hard", "reboot_soft", "rescue", "unrescue"]


class Command(SynnefoCommand):
    args = "<server_id>"
    help = "Modify a server."

    option_list = SynnefoCommand.option_list + (
        make_option(
            '--name',
            dest='name',
            metavar='NAME',
            help="Rename server."),
        make_option(
            '--user',
            dest='user',
            metavar='USER_UUID',
            help="Change ownership of server. Value must be a user UUID."
                 " This also changes the ownership of all volumes, NICs, and"
                 " IPs attached to the server. Finally, it assigns the"
                 " volumes, IPs, and the server to the system project of the"
                 " destination user."),
        make_option(
            "--suspended",
            dest="suspended",
            default=None,
            choices=["True", "False"],
            metavar="True|False",
            help="Mark a server as suspended/non-suspended."),
        make_option(
            "--flavor",
            dest="flavor",
            metavar="FLAVOR_ID",
            help="Resize a server by modifying its flavor. The new flavor"
                 " must have the same disk size and disk template."),
        make_option(
            "--action",
            dest="action",
            choices=ACTIONS,
            metavar="|".join(ACTIONS),
            help="Perform one of the allowed actions."),
        make_option(
            "--wait",
            dest="wait",
            default="True",
            choices=["True", "False"],
            metavar="True|False",
            help="Wait for Ganeti jobs to complete. [Default: True]"),
    )

    @convert_api_faults
    def handle(self, *args, **options):
        if len(args) != 1:
            raise CommandError("Please provide a server ID")

        server_id = args[0]
        server = get_resource("server", server_id)

        credentials = Credentials("snf-manage", is_admin=True)
        new_name = options.get("name", None)
        if new_name is not None:
            old_name = server.name
            server = servers.rename(server_id, new_name, credentials)
            self.stdout.write("Renamed server '%s' from '%s' to '%s'\n" %
                              (server, old_name, new_name))

        suspended = options.get("suspended", None)
        if suspended is not None:
            suspended = parse_bool(suspended)
            server = servers.suspend(server_id, suspended, credentials)
            self.stdout.write("Set server '%s' as suspended=%s\n" %
                              (server, suspended))

        new_owner = options.get('user')
        if new_owner is not None:
            if "@" in new_owner:
                raise CommandError("Invalid user UUID.")
            if new_owner == server.userid:
                self.stdout.write("%s is already server owner.\n" % new_owner)
            else:
                servers.change_owner(server_id, new_owner, credentials)
                self.stdout.write(
                    "WARNING: User quotas should be out of sync now,"
                    " run `snf-manage reconcile-resources-cyclades'"
                    " to review and update them.\n")

        wait = parse_bool(options["wait"])
        new_flavor_id = options.get("flavor")
        if new_flavor_id is not None:
            new_flavor = get_resource("flavor", new_flavor_id)
            old_flavor = server.flavor
            msg = "Resizing server '%s' from flavor '%s' to '%s'.\n"
            self.stdout.write(msg % (server, old_flavor, new_flavor))
            server = servers.resize(server_id, new_flavor, credentials)
            wait_server_task(server, wait, stdout=self.stdout)

        action = options.get("action")
        if action is not None:
            if action == "start":
                server = servers.start(server_id, credentials=credentials)
            elif action == "stop":
                server = servers.stop(server_id, credentials=credentials)
            elif action == "reboot_hard":
                server = servers.reboot(server_id, reboot_type="HARD",
                                        credentials=credentials)
            elif action == "reboot_soft":
                server = servers.reboot(server_id, reboot_type="SOFT",
                                        credentials=credentials)
            elif action == "rescue":
                server = servers.rescue(server_id, credentials=credentials)
            elif action == "unrescue":
                server = servers.unrescue(server_id, credentials=credentials)
            else:
                raise CommandError("Unknown action.")
            wait_server_task(server, wait, stdout=self.stdout)
