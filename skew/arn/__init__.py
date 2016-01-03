# Copyright 2014 Scopely, Inc.
# Copyright (c) 2015 Mitch Garnaat
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import re

from six.moves import zip_longest
import jmespath

import skew.resources
from skew.config import get_config

LOG = logging.getLogger(__name__)
DebugFmtString = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


class ARNComponent(object):

    def __init__(self, pattern, arn):
        self.pattern = pattern
        self._arn = arn

    def __repr__(self):
        return self.pattern

    def choices(self, context=None):
        """
        This method is responsible for returning all of the possible
        choices for the value of this component.

        The ``context`` can be a list of values of the components
        that precede this component.  The value of one or more of
        those previous components could affect the possible
        choices for this component.

        If no ``context`` is provided this method should return
        all possible choices.
        """
        return []

    def match(self, pattern, context=None):
        """
        This method returns a (possibly empty) list of strings that
        match the regular expression ``pattern`` provided.  You can
        also provide a ``context`` as described above.

        This method calls ``choices`` to get a list of all possible
        choices and then filters the list by performing a regular
        expression search on each choice using the supplied ``pattern``.
        """
        matches = []
        regex = pattern
        if regex == '*':
            regex = '.*'
        regex = re.compile(regex)
        for choice in self.choices(context):
            if regex.search(choice):
                matches.append(choice)
        return matches

    def matches(self, context=None):
        """
        This is a convenience method to return all possible matches
        filtered by the current value of the ``pattern`` attribute.
        """
        return self.match(self.pattern, context)

    def complete(self, prefix='', context=None):
        return [c for c in self.choices(context) if c.startswith(prefix)]


class Resource(ARNComponent):

    def _split_resource(self, resource):
        if '/' in resource:
            resource_type, resource_id = resource.split('/', 1)
        elif ':' in resource:
            resource_type, resource_id = resource.split(':', 1)
        else:
            resource_type = resource
            resource_id = None
        return (resource_type, resource_id)

    def match(self, pattern, context=None):
        resource_type, _ = self._split_resource(pattern)
        return super(Resource, self).match(resource_type, context)

    def choices(self, context=None):
        if context:
            service = context[2]
        else:
            service = self._arn.service.pattern
        all_resources = skew.resources.all_types(
            self._arn.provider.pattern, service)
        if not all_resources:
            all_resources = ['*']
        return all_resources

    def enumerate(self, context, aws_creds=None):
        LOG.debug('Resource.enumerate %s', context)
        _, provider, service_name, region, account = context
        resource_type, resource_id = self._split_resource(self.pattern)
        LOG.debug('resource_type=%s, resource_id=%s',
                  resource_type, resource_id)
        resources = []
        for resource_type in self.matches(context):
            resource_path = '.'.join([provider, service_name, resource_type])
            resource_cls = skew.resources.find_resource_class(resource_path)
            resources.extend(resource_cls.enumerate(
                self._arn, region, account, resource_id, aws_creds=aws_creds))
        return resources


class Account(ARNComponent):

    def __init__(self, pattern, arn):
        self._accounts = get_config()['accounts']
        super(Account, self).__init__(pattern, arn)

    def choices(self, context=None):
        return list(self._accounts.keys())

    def enumerate(self, context, aws_creds=None):
        LOG.debug('Account.enumerate %s', context)
        for match in self.matches(context):
            context.append(match)
            for resource in self._arn.resource.enumerate(
                    context, aws_creds=aws_creds):
                yield resource
            context.pop()


class Region(ARNComponent):
    _all_region_names = ['us-east-1',
                         'us-west-1',
                         'us-west-2',
                         'eu-west-1',
                         'eu-central-1',
                         'ap-southeast-1',
                         'ap-southeast-2',
                         'ap-northeast-1',
                         'sa-east-1']

    _region_names_limited = ['us-east-1',
                             'us-west-2',
                             'eu-west-1',
                             'ap-southeast-1',
                             'ap-southeast-2',
                             'ap-northeast-1']

    _no_region_required = ['']

    _service_region_map = {
        'redshift': _region_names_limited,
        'glacier': _region_names_limited,
        'kinesis': _region_names_limited,
        'iam': _no_region_required,
        'route53': _no_region_required,
        'lambda': ['us-east-1', 'us-west-2', 'eu-west-1', 'ap-northeast-1']
    }

    def choices(self, context=None):
        if context:
            service = context[2]
        else:
            service = self._arn.service
        return self._service_region_map.get(
            service, self._all_region_names)

    def enumerate(self, context, aws_creds=None):
        LOG.debug('Region.enumerate %s', context)
        for match in self.matches(context):
            context.append(match)
            for account in self._arn.account.enumerate(
                    context, aws_creds=aws_creds):
                yield account
            context.pop()


class Service(ARNComponent):

    def choices(self, context=None):
        if context:
            provider = context[1]
        else:
            provider = self._arn.provider.pattern
        return skew.resources.all_services(provider)

    def enumerate(self, context, aws_creds=None):
        LOG.debug('Service.enumerate %s', context)
        for match in self.matches(context):
            context.append(match)
            for region in self._arn.region.enumerate(
                    context, aws_creds=aws_creds):
                yield region
            context.pop()


class Provider(ARNComponent):

    def choices(self, context=None):
        return ['aws']

    def enumerate(self, context, aws_creds=None):
        LOG.debug('Provider.enumerate %s', context)
        for match in self.matches(context):
            context.append(match)
            for service in self._arn.service.enumerate(
                    context, aws_creds=aws_creds):
                yield service
            context.pop()


class Scheme(ARNComponent):

    def choices(self, context=None):
        return ['arn']

    def enumerate(self, context, aws_creds=None):
        LOG.debug('Scheme.enumerate %s', context)
        for match in self.matches(context):
            context.append(match)
            for provider in self._arn.provider.enumerate(
                    context, aws_creds=aws_creds):
                yield provider
            context.pop()


class ARN(object):

    ComponentClasses = [Scheme, Provider, Service, Region, Account, Resource]

    def __init__(self, arn_string='arn:aws:*:*:*:*', aws_creds=None):
        self.query = None
        self._components = None
        self._build_components_from_string(arn_string)
        self.aws_creds = aws_creds

    def __repr__(self):
        return ':'.join([str(c) for c in self._components])

    def debug(self):
        self.set_logger('skew', logging.DEBUG)

    def set_logger(self, logger_name, level=logging.DEBUG):
        """
        Convenience function to quickly configure full debug output
        to go to the console.
        """
        log = logging.getLogger(logger_name)
        log.setLevel(level)

        ch = logging.StreamHandler(None)
        ch.setLevel(level)

        # create formatter
        formatter = logging.Formatter(DebugFmtString)

        # add formatter to ch
        ch.setFormatter(formatter)

        # add ch to logger
        log.addHandler(ch)

    def _build_components_from_string(self, arn_string):
        if '|' in arn_string:
            arn_string, query = arn_string.split('|')
            self.query = jmespath.compile(query)
        pairs = zip_longest(
            self.ComponentClasses, arn_string.split(':', 6), fillvalue='*')
        self._components = [c(n, self) for c, n in pairs]

    @property
    def scheme(self):
        return self._components[0]

    @property
    def provider(self):
        return self._components[1]

    @property
    def service(self):
        return self._components[2]

    @property
    def region(self):
        return self._components[3]

    @property
    def account(self):
        return self._components[4]

    @property
    def resource(self):
        return self._components[5]

    def __iter__(self):
        context = []
        for scheme in self.scheme.enumerate(context, aws_creds=self.aws_creds):
            yield scheme
