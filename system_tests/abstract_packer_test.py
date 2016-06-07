########
# Copyright (c) 2015 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#    * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    * See the License for the specific language governing permissions and
#    * limitations under the License.

import json
import os
import shutil
import subprocess
import tempfile
import time
from abc import ABCMeta, abstractmethod

from cosmo_tester.framework import git_helper as git

from retrying import retry
from requests import ConnectionError
from cloudify_cli import constants
from cloudify_rest_client.exceptions import CloudifyClientError
from cosmo_tester.framework.util import create_rest_client
from cosmo_tester.framework.cfy_helper import CfyHelper

DEFAULT_IMAGE_BAKERY_REPO_URL = 'https://github.com/' \
                                'cloudify-cosmo/cloudify-image-bakery.git'
DEFAULT_PACKER_URL = 'https://releases.hashicorp.com/packer/' \
                     '0.10.0/packer_0.10.0_linux_amd64.zip'
DEFAULT_PACKER_FILE = 'cloudify.json'
DEFAULT_CLOUDIFY_VERSION = 'master'
DEFAULT_AMI = "ami-91feb7fb"
DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_AWS_INSTANCE_TYPE = 'm3.large'
SUPPORTED_ENVS = [
    'aws',
    'openstack',
]


class AbstractPackerTest(object):
    __metaclass__ = ABCMeta

    secure = False

    @abstractmethod
    def _delete_image(self): pass

    @abstractmethod
    def _find_image(self): pass

    @abstractmethod
    def deploy_image(self): pass

    @abstractmethod
    def _undeploy_image(self): pass

    def setUp(self):
        super(AbstractPackerTest, self).setUp()

        self.conf = self.env.cloudify_config
        self.config_inputs = {}

    def _find_images(self):
        for environment in self.images.keys():
            self.images[environment] = self._find_image()

    def delete_images(self):
        for environment in self.images.keys():
            self._delete_image(self.images[environment])

    def _get_packer(self, destination):
        packer_url = self.env.cloudify_config.get(
            'packer_url',
            DEFAULT_PACKER_URL
        )
        wget_command = [
            'wget',
            packer_url
        ]
        wget_status = subprocess.call(
            wget_command,
            cwd=destination,
        )
        assert wget_status == 0

        unzip_command = [
            'unzip',
            os.path.split(packer_url)[1],
        ]
        unzip_status = subprocess.call(
            unzip_command,
            cwd=destination,
        )
        assert unzip_status == 0

        return os.path.join(destination, 'packer')

    def _get_marketplace_image_bakery_repo(self):
        self.base_temp_dir = tempfile.mkdtemp()

        url = self.env.cloudify_config.get(
            'image_bakery_url',
            DEFAULT_IMAGE_BAKERY_REPO_URL
        )

        git.clone(
            url=url,
            basedir=self.base_temp_dir,
            branch=self.env.cloudify_config.get('image_bakery_branch'),
        )

        self.addCleanup(self.clean_temp_dir)

        repo_path = os.path.join(
            self.base_temp_dir,
            'git',
            'cloudify-image-bakery',
        )
        return repo_path

    def _build_inputs(self, destination_path, name_prefix):
        openstack_url = self.env.cloudify_config.get('keystone_url')
        if openstack_url is not None:
            # TODO: Do a join on this if the URL doesn't have 2.0 already
            openstack_url += '/v2.0/'
        else:
            openstack_url = 'OPENSTACK IDENTITY ENDPOINT NOT SET'
        # Provide 'not set' defaults for most to allow for running e.g. just
        # the openstack tests without complaining about lack of aws settings
        self.build_inputs = {
            "name_prefix": name_prefix,
            "cloudify_version": self.env.cloudify_config.get(
                'marketplace_cloudify_version',
                DEFAULT_CLOUDIFY_VERSION
            ),
            "aws_access_key": self.env.cloudify_config.get(
                'aws_access_key',
                'AWS ACCESS KEY NOT SET'
            ),
            "aws_secret_key": self.env.cloudify_config.get(
                'aws_secret_key',
                'AWS SECRET KEY NOT SET'
            ),
            "aws_source_ami": self.env.cloudify_config.get(
                'marketplace_source_ami',
                DEFAULT_AMI
            ),
            "aws_region": self.env.cloudify_config.get(
                'aws_region',
                DEFAULT_AWS_REGION
            ),
            "aws_instance_type": self.env.cloudify_config.get(
                'aws_instance_type',
                DEFAULT_AWS_INSTANCE_TYPE
            ),
            "openstack_ssh_keypair_name": self.env.cloudify_config.get(
                'openstack_ssh_keypair',
                'OPENSTACK SSH KEYPAIR NOT SET'
            ),
            "openstack_availability_zone": self.env.cloudify_config.get(
                'openstack_marketplace_availability_zone',
                'OPENSTACK AVAILABILITY ZONE NOT SET'
            ),
            "openstack_image_flavor": self.env.cloudify_config.get(
                'openstack_marketplace_flavor',
                'OPENSTACK FLAVOR NOT SET'
            ),
            "openstack_identity_endpoint": openstack_url,
            "openstack_source_image_id": self.env.cloudify_config.get(
                'openstack_marketplace_source_image',
                'OPENSTACK SOURCE IMAGE NOT SET'
            ),
            "openstack_username": self.env.cloudify_config.get(
                'keystone_username',
                'OPENSTACK USERNAME NOT SET'
            ),
            "openstack_password": self.env.cloudify_config.get(
                'keystone_password',
                'OPENSTACK PASSWORD NOT SET'
            ),
            "openstack_tenant_name": self.env.cloudify_config.get(
                'keystone_tenant_name',
                'OPENSTACK TENANT NAME NOT SET'
            ),
            "openstack_floating_ip_pool_name": self.env.cloudify_config.get(
                'openstack_floating_ip_pool_name',
                'OPENSTACK FLOATING IP POOL NOT SET'
            ),
            "openstack_network": self.env.cloudify_config.get(
                'openstack_network',
                'OPENSTACK NETWORK NOT SET'
            ),
            "openstack_security_group": self.env.cloudify_config.get(
                'openstack_security_group',
                'OPENSTACK SECURITY GROUP NOT SET'
            ),
            "cloudify_manager_security_enabled":
                'true' if self.secure else 'false',
        }
        inputs = json.dumps(self.build_inputs)
        with open(destination_path, 'w') as inputs_handle:
            inputs_handle.write(inputs)

    def build_with_packer(self,
                          name_prefix='marketplace-system-tests',
                          only=None,
                          ):
        self.name_prefix = name_prefix
        if only is None:
            self.images = {environment: None for environment in SUPPORTED_ENVS}
        else:
            self.images = {only: None}

        self._check_for_images(should_exist=False)

        image_bakery_repo_path = self._get_marketplace_image_bakery_repo()

        marketplace_path = os.path.join(
            image_bakery_repo_path,
            'cloudify_marketplace',
        )

        packer_bin = self._get_packer(marketplace_path)

        inputs_file_name = 'system-test-inputs.json'
        self._build_inputs(
            destination_path=os.path.join(
                marketplace_path,
                inputs_file_name
            ),
            name_prefix=name_prefix,
        )

        # Build the packer command
        command = [
            packer_bin,
            'build',
            '--var-file={inputs}'.format(inputs=inputs_file_name),
        ]
        if only is not None:
            command.append('--only={only}'.format(only=only))
        command.append(self.env.cloudify_config.get(
            'packer_file',
            DEFAULT_PACKER_FILE
        ))

        # Run packer
        self.logger.info(command)
        build_status = subprocess.call(
            command,
            cwd=marketplace_path,
        )
        assert build_status == 0

        # A test on AWS failed due to no image being found despite the image
        # existing, so we will put a small delay here to reduce the chance of
        # that recurring
        # It would be nice if there were a better way to do this, but it
        # depends on the environment and maximum wait time is unclear
        time.sleep(15)

        self._check_for_images()

        self.addCleanup(self.delete_images)

    def _check_for_images(self, should_exist=True):
        self._find_images()
        for env, image in self.images.items():
            if should_exist:
                fail = 'Image for {env} not found!'.format(env=env)
                assert image is not None, fail
            else:
                fail = 'Image for {env} already exists!'.format(env=env)
                assert image is None, fail

    def clean_temp_dir(self):
        shutil.rmtree(self.base_temp_dir)

    def get_public_ip(self, nodes_state):
        return self.manager_public_ip

    @property
    def expected_nodes_count(self):
        return 4

    @property
    def host_expected_runtime_properties(self):
        return []

    @property
    def entrypoint_node_name(self):
        return 'host'

    @property
    def entrypoint_property_name(self):
        return 'ip'

    def _deploy_manager(self):
        self.build_with_packer(only=self.packer_build_only)
        self.deploy_image()

        os.environ[constants.CLOUDIFY_USERNAME_ENV] = 'cloudify'
        os.environ[constants.CLOUDIFY_PASSWORD_ENV] = 'cloudify'

        self.client = create_rest_client(
            self.manager_public_ip,
            secure=self.secure,
            trust_all=self.secure,
        )

        response = {'status': None}
        attempt = 0
        max_attempts = 80
        while response['status'] != 'running':
            attempt += 1
            if attempt >= max_attempts:
                raise RuntimeError('Manager did not start in time')
            else:
                time.sleep(3)
            try:
                response = self.client.manager.get_status()
            except CloudifyClientError:
                # Manager not fully ready
                pass
            except ConnectionError:
                # Timeout
                pass

        self.agents_secgroup = self.conf.get(
            'system-tests-security-group-name',
            'marketplace-system-tests-security-group')
        self.agents_keypair = self.conf.get(
            'system-tests-keypair-name',
            'marketplace-system-tests-keypair')

        self.config_inputs.update({
            'agents_security_group_name': self.agents_secgroup,
            'agents_keypair_name': self.agents_keypair,
            })
        if self.secure:
            # Need to add the external IP address to the generated cert
            self.config_inputs.update({
                'manager_names_and_ips': self.manager_public_ip,
                })

        # Arbitrary sleep to wait for manager to actually finish starting as
        # otherwise we suffer timeouts in the next section
        # TODO: This would be better if it actually had some way of checking
        # the manager was fully up and we had a reasonable upper bound on how
        # long we should expect to wait for that
        time.sleep(90)

        # We have to retry this a few times, as even after the manager is
        # accessible we still see failures trying to create deployments
        deployment_created = False
        attempt = 0
        max_attempts = 40
        while not deployment_created:
            attempt += 1
            if attempt >= max_attempts:
                raise RuntimeError('Manager not created in time')
            else:
                time.sleep(3)
            try:
                self.client.deployments.create(
                    blueprint_id='CloudifySettings',
                    deployment_id='config',
                    inputs=self.config_inputs,
                )
                self.addCleanup(self._delete_agents_secgroup)
                self.addCleanup(self._delete_agents_keypair)
                deployment_created = True
            except Exception as err:
                if attempt >= max_attempts:
                    raise err
                else:
                    self.logger.warn(
                        'Saw error {}. Retrying.'.format(str(err))
                    )

        attempt = 0
        max_attempts = 40
        execution_started = False
        while not execution_started:
            attempt += 1
            if attempt >= max_attempts:
                raise RuntimeError('Manager did not start in time')
            else:
                time.sleep(3)
            try:
                self.client.executions.start(
                    deployment_id='config',
                    workflow_id='install',
                )
                execution_started = True
            except Exception as err:
                if attempt >= max_attempts:
                    raise err
                else:
                    self.logger.warn(
                        'Saw error {}. Retrying.'.format(str(err))
                    )

    @retry(stop_max_delay=180000, wait_exponential_multiplier=1000)
    def cfy_connect(self, *args, **kwargs):
        self.logger.debug('Attempting to set up CfyHelper: {} {}'.format(
            args, kwargs))
        return CfyHelper(*args, **kwargs)

    def test_hello_world(self):
        self._deploy_manager()

        self.cfy = self.cfy_connect(management_ip=self.manager_public_ip)

        # once we've managed to connect again using `cfy use` we need to update
        # the rest client too:
        self.client = create_rest_client(self.manager_public_ip)

        time.sleep(120)

        self._run(
            blueprint_file='ec2-vpc-blueprint.yaml',
            inputs={
                'agent_user': 'ubuntu',
                'image_id': self.conf['aws_trusty_image_id'],
                'vpc_id': self.conf['aws_vpc_id'],
                'vpc_subnet_id': self.conf['aws_subnet_id'],
            },
            influx_host_ip=self.manager_public_ip,
        )


class AbstractSecureTest(AbstractPackerTest):
    secure = True

    def setUp(self, *args, **kwargs):
        super(AbstractSecureTest, self).setUp(*args, **kwargs)

        self.config_inputs.update({
            'new_manager_username': self.conf.get('new_manager_username',
                                                  'new'),
            'new_manager_password': self.conf.get('new_manager_password',
                                                  'new'),
            'new_broker_username': self.conf.get('new_broker_username', 'new'),
            'new_broker_password': self.conf.get('new_broker_password', 'new'),
            'broker_names_and_ips': self.conf.get('broker_names_and_ips',
                                                  'test'),
            })

        os.environ['CLOUDIFY_SSL_TRUST_ALL'] = 'True'

    def test_hello_world(self):
        self._deploy_manager()

        os.environ[constants.CLOUDIFY_USERNAME_ENV
                   ] = self.conf.get('new_manager_username', 'new')
        os.environ[constants.CLOUDIFY_PASSWORD_ENV
                   ] = self.conf.get('new_manager_password', 'new')

        self.cfy = self.cfy_connect(
            management_ip=self.manager_public_ip,
            port=443,
            )

        # once we've managed to connect again using `cfy use` we need to update
        # the rest client too:
        self.client = create_rest_client(
            self.manager_public_ip,
            secure=True,
            trust_all=True,
        )

        time.sleep(120)

        self._run(
            blueprint_file='ec2-vpc-blueprint.yaml',
            inputs={
                'agent_user': 'ubuntu',
                'image_id': self.conf['aws_trusty_image_id'],
                'vpc_id': self.conf['aws_vpc_id'],
                'vpc_subnet_id': self.conf['aws_subnet_id'],
            },
            influx_host_ip=self.manager_public_ip,
        )
