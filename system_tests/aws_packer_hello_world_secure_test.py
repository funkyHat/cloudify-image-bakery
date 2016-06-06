import os
import time

from retrying import retry
from cloudify_cli import constants
from cosmo_tester.framework.util import create_rest_client
from cosmo_tester.test_suites.test_blueprints.hello_world_bash_test import \
    AbstractHelloWorldTest
from cosmo_tester.framework.cfy_helper import CfyHelper

from abstract_packer_test import AbstractPackerTest


class AWSHelloWorldSecureTest(AbstractHelloWorldTest, AbstractPackerTest):
    def setUp(self, *args, **kwargs):
        super(AWSHelloWorldSecureTest, self).setUp(*args, **kwargs)
        AbstractPackerTest.setUp(self, *args, **kwargs)

        self.aws_hello_world_test_config_inputs.update({
            'new_manager_username': self.conf.get('new_manager_username',
                                                  'new'),
            'new_manager_password': self.conf.get('new_manager_password',
                                                  'new'),
            'new_broker_username': self.conf.get('new_broker_username', 'new'),
            'new_broker_password': self.conf.get('new_broker_password', 'new'),
            'broker_names_and_ips': self.conf.get('broker_names_and_ips',
                                                  'test'),
            })

        self.secure = True

        os.environ['CLOUDIFY_SSL_TRUST_ALL'] = 'True'

    @retry(stop_max_delay=180000, wait_exponential_multiplier=1000)
    def cfy_connect(self, *args, **kwargs):
        self.logger.debug('Attempting to set up CfyHelper: {} {}'.format(
            args, kwargs))
        return CfyHelper(*args, **kwargs)

    def test_hello_world_secure_aws(self):
        self._deploy_manager(secure=True, trust_all=True)

        os.environ[constants.CLOUDIFY_USERNAME_ENV
            ] = self.conf.get('new_manager_username', 'new')
        os.environ[constants.CLOUDIFY_PASSWORD_ENV
            ] = self.conf.get('new_manager_password', 'new')

        self.cfy = self.cfy_connect(
            management_ip=self.aws_manager_public_ip,
            port=443,
            )

        # once we've managed to connect again using `cfy use` we need to update
        # the rest client too:
        self.client = create_rest_client(
            self.aws_manager_public_ip,
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
            influx_host_ip=self.aws_manager_public_ip,
        )
