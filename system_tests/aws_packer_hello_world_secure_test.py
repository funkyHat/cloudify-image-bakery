
from cosmo_tester.test_suites.test_blueprints.hello_world_bash_test import \
    AbstractHelloWorldTest

from .abstract_packer_test import AbstractSecureTest
from .abstract_aws_test import AbstractAwsTest


class AWSHelloWorldSecureTest(
        AbstractSecureTest,
        AbstractAwsTest,
        AbstractHelloWorldTest,
        ):
    pass
