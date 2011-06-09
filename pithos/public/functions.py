# Copyright 2011 GRNET S.A. All rights reserved.
# 
# Redistribution and use in source and binary forms, with or
# without modification, are permitted provided that the following
# conditions are met:
# 
#   1. Redistributions of source code must retain the above
#      copyright notice, this list of conditions and the following
#      disclaimer.
# 
#   2. Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials
#      provided with the distribution.
# 
# THIS SOFTWARE IS PROVIDED BY GRNET S.A. ``AS IS'' AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL GRNET S.A OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
# 
# The views and conclusions contained in the software and
# documentation are those of the authors and should not be
# interpreted as representing official policies, either expressed
# or implied, of GRNET S.A.

import logging
import uuid

from django.http import HttpResponse

from pithos.api.faults import (Fault, BadRequest, ItemNotFound, RangeNotSatisfiable)
from pithos.api.util import (put_object_meta, validate_modification_preconditions,
    validate_matching_preconditions, get_range, ObjectWrapper, api_method)
from pithos.backends import backend


logger = logging.getLogger(__name__)


def object_demux(request, v_account, v_container, v_object):
    if request.method == 'HEAD':
        return object_meta(request, v_account, v_container, v_object)
    elif request.method == 'GET':
        return object_read(request, v_account, v_container, v_object)
    else:
        return method_not_allowed(request)

# TODO: Use a version of api_method that does not check for a token.

@api_method('HEAD')
def object_meta(request, v_account, v_container, v_object):
    # Normal Response Codes: 204
    # Error Response Codes: serviceUnavailable (503),
    #                       itemNotFound (404),
    #                       unauthorized (401),
    #                       badRequest (400)
    
    try:
        meta = backend.get_object_meta(request.user, v_container, v_object)
    except NameError:
        raise ItemNotFound('Object does not exist')
    
    if 'X-Object-Public' not in meta:
        raise ItemNotFound('Object does not exist')
    
    response = HttpResponse(status=204)
    put_object_meta(response, meta)
    return response

@api_method('GET')
def object_read(request, v_account, v_container, v_object):
    # Normal Response Codes: 200, 206
    # Error Response Codes: serviceUnavailable (503),
    #                       rangeNotSatisfiable (416),
    #                       preconditionFailed (412),
    #                       itemNotFound (404),
    #                       unauthorized (401),
    #                       badRequest (400),
    #                       notModified (304)
    
    try:
        meta = backend.get_object_meta(request.user, v_container, v_object)
    except NameError:
        raise ItemNotFound('Object does not exist')
    
    if 'X-Object-Public' not in meta:
        raise ItemNotFound('Object does not exist')
    
    # Evaluate conditions.
    validate_modification_preconditions(request, meta)
    try:
        validate_matching_preconditions(request, meta)
    except NotModified:
        response = HttpResponse(status=304)
        response['ETag'] = meta['hash']
        return response
    
    try:
        size, hashmap = backend.get_object_hashmap(request.user, v_container, v_object)
    except NameError:
        raise ItemNotFound('Object does not exist')
    
    # Range handling.
    ranges = get_range(request, size)
    if ranges is None:
        ranges = [(0, size)]
        ret = 200
    else:
        check = [True for offset, length in ranges if
                    length <= 0 or length > size or
                    offset < 0 or offset >= size or
                    offset + length > size]
        if len(check) > 0:
            raise RangeNotSatisfiable('Requested range exceeds object limits')        
        ret = 206
    
    if ret == 206 and len(ranges) > 1:
        boundary = uuid.uuid4().hex
    else:
        boundary = ''
    wrapper = ObjectWrapper(request.user, v_container, v_object, ranges, size, hashmap, boundary)
    response = HttpResponse(wrapper, status=ret)
    put_object_meta(response, meta)
    if ret == 206:
        if len(ranges) == 1:
            offset, length = ranges[0]
            response['Content-Length'] = length # Update with the correct length.
            response['Content-Range'] = 'bytes %d-%d/%d' % (offset, offset + length - 1, size)
        else:
            del(response['Content-Length'])
            response['Content-Type'] = 'multipart/byteranges; boundary=%s' % (boundary,)
    return response

@api_method()
def method_not_allowed(request):
    raise ItemNotFound('Object does not exist')
