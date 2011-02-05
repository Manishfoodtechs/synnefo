#
# Unit Tests for db
#
# Provides automated tests for db module
#
# Copyright 2010 Greek Research and Technology Network
#

import unittest

from datetime import datetime, date

from db.models import *
from db import credit_allocator
from db import charger

from django.conf import settings

class CreditAllocatorTestCase(unittest.TestCase):
    def setUp(self):
        """Setup the test"""
        user = OceanUser(pk=1, name='Test User', credit=0, quota=100, monthly_rate=10)
        user.created = datetime.datetime.now()
        user.save()
    
    def tearDown(self):
        """Cleaning up the data"""
        user = OceanUser.objects.get(pk=1)
        user.delete()
    
    def test_credit_allocator(self):
        """Credit Allocator unit test method"""
        # test the allocator
        credit_allocator.allocate_credit() 
               
        user = OceanUser.objects.get(pk=1)
        self.assertEquals(user.credit, 10, 'Allocation of credits failed, credit: %d (should be 10)' % ( user.credit, ) )
        
        # test if the quota policy is endorced
        for i in range(1, 10):
            credit_allocator.allocate_credit()
        
        user = OceanUser.objects.get(pk=1)
        self.assertEquals(user.credit, user.quota, 'User exceeded quota! (cr:%d, qu:%d)' % ( user.credit, user.quota) )


class FlavorTestCase(unittest.TestCase):
    def setUp(self):
        """Setup the test"""
        # Add the Flavor object
        flavor = Flavor(pk=1, cpu=10, ram=10, disk=10)
        flavor.save()
        
        # Add the FlavorCostHistory
        fch = FlavorCostHistory(pk=1, cost_active=10, cost_inactive=5)
        fch.effective_from = date(day=01, month=01, year=2011)
        fch.flavor = flavor
        fch.save()
        
        fch = FlavorCostHistory(pk=2, cost_active=2, cost_inactive=1)
        fch.effective_from = date(day=01, month=01, year=2010)
        fch.flavor = flavor
        fch.save()
        
    def tearDown(self):
        """Cleaning up the data"""
        flavor = Flavor.objects.get(pk=1)
        flavor.delete()
        
        fch = FlavorCostHistory(pk=2)
        fch.delete()
                
    def test_flavor(self):
        """Test a flavor object, its internal cost calculation and naming methods"""
        flavor = Flavor.objects.get(pk=1)
        
        self.assertEquals(flavor.cost_active, 10, 'Active cost is not calculated correctly! (%d!=10)' % ( flavor.cost_active, ) )
        self.assertEquals(flavor.cost_inactive, 5, 'Inactive cost is not calculated correctly! (%d!=5)' % ( flavor.cost_inactive, ) )
        self.assertEquals(flavor.name, u'C10R10D10', 'Invalid flavor name!')


class ChargerTestcase(unittest.TestCase):
    def setUp(self):
        """Setup the test"""
        # add a user
        user = OceanUser(pk=1, name='Test User', credit=100, quota=100, monthly_rate=10)
        user.created = datetime.datetime.now()
        user.save()
        
        # add a Flavor 
        flavor = Flavor(pk=1, cpu=10, ram=10, disk=10)
        flavor.save()
        
        # and fill the pricing list
        fch = FlavorCostHistory(pk=1, cost_active=10, cost_inactive=5)
        fch.effective_from = date(day=01, month=01, year=2010)
        fch.flavor = flavor
        fch.save()
        
        # Now, add a VM
        vm = VirtualMachine(pk=1)
        vm.created = datetime.datetime.now()
        vm.state = 'PE_VM_RUNNING'
        vm.charged = datetime.datetime.now()
        vm.imageid = 1
        vm.hostid = 'testhostid'
        vm.server_label = 'agreatserver'
        vm.image_version = '1.0.0'
        vm.ipfour = '127.0.0.1'
        vm.ipsix = '2001:0db8:85a3:0000:0000:8a2e:0370:7334'
        vm.owner = user
        vm.flavor = flavor
        
        vm.save()
        
    def tearDown(self):
        """Cleaning up the data"""
        user = OceanUser.objects.get(pk=1)
        user.delete()
        
        flavor = Flavor.objects.get(pk=1)
    
    def test_charger(self):
        """Charger unit test method"""
        
        # charge when the vm is running
        charger.charge()
        
        user = OceanUser.objects.get(pk=1)
        self.assertEquals(user.credit, 90, 'Error in charging process (%d!=90, running)' % ( user.credit, ))
        
        # charge when the vm is stopped
        vm = VirtualMachine.objects.get(pk=1)
        vm.state = 'PE_VM_STOPPED'
        vm.save()
        
        charger.charge()
        
        user = OceanUser.objects.get(pk=1)
        self.assertEquals(user.credit, 85, 'Error in charging process (%d!=85, stopped)' % ( user.credit, ))
        
        # try charge until the user spends all his credits, see if the charger
        vm = VirtualMachine.objects.get(pk=1)
        vm.state = 'PE_VM_RUNNING'
        vm.save()
        
        # the user now has 85, charge until his credits drop to zero
        for i in range(1, 10):
            charger.charge()
        
        user = OceanUser.objects.get(pk=1)
        self.assertEquals(user.credit, 0, 'Error in charging process (%d!=0, running)' % ( user.credit, ))
        
        
class VirtualMachineTestCase(unittest.TestCase):
    def setUp(self):
        """Setup the test"""
        # Add a user
        user = OceanUser(pk=1, name='Test User', credit=100, quota=100, monthly_rate=10)
        user.created = datetime.datetime.now()
        user.save()
        
        # add a Flavor 
        flavor = Flavor(pk=1, cpu=10, ram=10, disk=10)
        flavor.save()
        
        # Now, add a VM
        vm = VirtualMachine(pk=1)
        vm.created = datetime.datetime.now()
        vm.state = 'PE_VM_RUNNING'
        vm.charged = datetime.datetime.now()
        vm.imageid = 1
        vm.hostid = 'testhostid'
        vm.server_label = 'agreatserver'
        vm.image_version = '1.0.0'
        vm.ipfour = '127.0.0.1'
        vm.ipsix = '2001:0db8:85a3:0000:0000:8a2e:0370:7334'
        vm.owner = user
        vm.flavor = flavor
        
        vm.save()
        
        
    def tearDown(self):
        """Cleaning up the data"""
        user = OceanUser.objects.get(pk=1)
        user.delete()
        
        flavor = Flavor.objects.get(pk=1)
        flavor.delete()
        
        
    def test_virtual_machine(self):
        """VirtualMachine (model) unit test"""
        
        