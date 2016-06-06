########
# Copyright (c) 2014 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#    * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    * See the License for the specific language governing permissions and
#    * limitations under the License.

import time

from cosmo_tester.test_suites.test_blueprints.hello_world_bash_test import \
    AbstractHelloWorldTest
from cosmo_tester.test_suites.test_marketplace_image_builder\
    .abstract_packer_test import AbstractPackerTest
from cosmo_tester.framework.cfy_helper import CfyHelper


class AWSHelloWorldTest(AbstractPackerTest, AbstractHelloWorldTest):
    def setUp(self):
        super(AWSHelloWorldTest, self).setUp()

        self.secure = False

    def test_hello_world_aws(self):
        self._deploy_manager()

        self.cfy = CfyHelper(management_ip=self.aws_manager_public_ip)

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
