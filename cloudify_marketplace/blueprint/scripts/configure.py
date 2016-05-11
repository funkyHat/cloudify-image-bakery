import base64
import json
import os
import re
import socket
import subprocess
import time

from cloudify import ctx
from cloudify.state import ctx_parameters as inputs


MANAGER_SSL_CERT_PATH = '/root/cloudify/server.crt'
MANAGER_TMP_PATH = os.path.join(os.path.expanduser('~'), 'cfy-tmp')


def regenerate_host_keys():
    # Do not regenerate moduli as it will take an excessive amount of time
    # and leave existing file alone in case it was generated by the user

    # regenerate ecdsa key
    subprocess.call(["rm", "/etc/ssh/ssh_host_ecdsa_key"])
    subprocess.call(["rm", "/etc/ssh/ssh_host_ecdsa_key.pub"])
    subprocess.call(["ssh-keygen", "-b", "521", "-f",
                     "/etc/ssh/ssh_host_ecdsa_key", "-N", '', "-t", "ecdsa"])

    # regenerate ed25519 key
    subprocess.call(["rm", "/etc/ssh/ssh_host_ed25519_key"])
    subprocess.call(["rm", "/etc/ssh/ssh_host_ed25519_key.pub"])
    subprocess.call(["ssh-keygen", "-f", "/etc/ssh/ssh_host_ed25519_key",
                     "-N", '', "-t", "ed25519"])

    # regenerate rsa key
    subprocess.call(["rm", "/etc/ssh/ssh_host_rsa_key"])
    subprocess.call(["rm", "/etc/ssh/ssh_host_rsa_key.pub"])
    subprocess.call(["ssh-keygen", "-b", "4096", "-f",
                     "/etc/ssh/ssh_host_rsa_key", "-N", '', "-t", "rsa"])

    # restart ssh
    subprocess.call(["systemctl", "restart", "sshd"])


def authorize_user_ssh_key(ssh_key):
    with open('/home/centos/.ssh/authorized_keys', 'a') as auth_handle:
        auth_handle.write('%s\n' % ssh_key)


def get_auth_header(username, password):
    header = None

    if username and password:
        credentials = '{0}:{1}'.format(username, password)
        header = {
            'Authorization':
            'Basic' + ' ' + base64.urlsafe_b64encode(credentials)}

    return header


def build_certs(private_key_path,
                public_key_path,
                subjectaltnames,
                openssl_conf_path='/etc/pki/tls/openssl.cnf'):
    subjectaltnames = subjectaltnames.split(',')

    common_name = subjectaltnames[0]
    subjectaltnames = set(subjectaltnames)

    subject_altdns = [
        'DNS:{name}'.format(name=name)
        for name in subjectaltnames
    ]
    subject_altips = []
    for name in subjectaltnames:
        ip_address = False
        try:
            socket.inet_pton(socket.AF_INET, name)
            ip_address = True
        except socket.error:
            # Not IPv4
            pass
        try:
            socket.inet_pton(socket.AF_INET6, name)
            ip_address = True
        except socket.error:
            # Not IPv6
            pass
        if ip_address:
            subject_altips.append('IP:{name}'.format(name=name))

    subjectaltnames = ','.join([
        ','.join(subject_altdns),
        ','.join(subject_altips),
    ])

    subprocess.call([
        'bash', '-c',
        'openssl req -x509 -nodes -newkey rsa:2048 -keyout {private_key_path} '
        '-out {public_key_path} -days 3650 -batch -subj "/CN={common_name}" '
        '-reqexts SAN -extensions SAN -config <(cat {openssl_conf_path} '
        '<(printf "[SAN]\nsubjectAltName={subjectaltnames}") )'.format(
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            common_name=common_name,
            openssl_conf_path=openssl_conf_path,
            subjectaltnames=subjectaltnames,
        )
    ])


def get_host_ips():
    # Get addresses for each interface that is active (up),
    # and only for dynamic or permanent IPv6 addresses
    ip_details = subprocess.check_output([
        'ip', 'address', 'show', 'up', 'dynamic', 'permanent',
    ])

    # IP addresses are expected to be in one of these forms:
    # inet <address>/<mask>
    # inet6 <address>/<mask>
    # We use a non-capturing group (?:) to ignore the inet/inet6 part
    ip_finder = re.compile('(?:inet[6]? )([^/]+)')

    return ip_finder.findall(ip_details)


def get_general_subjectaltnames(subjectaltnames):
    # TODO: Name this function better
    subjectaltnames = subjectaltnames.split(',')
    subjectaltnames = [
        name for name in subjectaltnames
        if name != ''
    ]
    subjectaltnames = ','.join(subjectaltnames)
    # Make sure localhost and the currently assigned IPs work
    host_ips = ','.join(get_host_ips())
    subjectaltnames = ','.join([
        subjectaltnames,
        host_ips,
        '127.0.0.1',
        'localhost',
    ])
    return subjectaltnames


def regenerate_manager_certificates(subjectaltnames):
    subjectaltnames = get_general_subjectaltnames(subjectaltnames)

    private_cert_path = '/root/cloudify/server.key'
    public_cert_path = MANAGER_SSL_CERT_PATH
    tmp_private = os.path.join(MANAGER_TMP_PATH, 'manager-private')
    tmp_public = os.path.join(MANAGER_TMP_PATH, 'manager-public')
    build_certs(
        private_key_path=tmp_private,
        public_key_path=tmp_public,
        subjectaltnames=subjectaltnames,
    )
    new_certs = [
        (tmp_private, private_cert_path),
        (tmp_public, public_cert_path),
    ]

    # Make sure the public cert is available for easy download from the UI
    with open(tmp_public) as cert_handle:
        ctx.instance.runtime_properties['manager_public_cert'] = \
            cert_handle.read()

    services_to_restart = [
        'nginx',
        'mgmtworker',
    ]
    return services_to_restart, new_certs


def replace_certs_and_restart_services_after_workflow(services, new_certs):

    # TODO: Make this a script that runs every minute called by manager:
    # If <file> exists, json.load file
    # while <file>.execution_id != execution finished:
    #   sleep(0.5)
    # <apply changes>

    # new_certs is expected to be a list of tuples with
    # [(<new cert path>, <path to original cert (to replace)>), ...]
    restart_file_path = os.path.join(
        MANAGER_TMP_PATH, 'cloudify_configuration_restart_services')

    with open(restart_file_path, 'w') as restart_file_handle:
        restart_file_handle.write('#! /usr/bin/env bash\n')

        # Stop the services
        for service in services:
            restart_file_handle.write('service %s stop\n' % service)

        # Copy certs to their destinations
        for cert, destination in new_certs:
            restart_file_handle.write('cp {cert} {destination}'.format(
                cert=cert,
                destination=destination,
            ))

        # Start the services again
        for service in services:
            restart_file_handle.write('service %s start\n' % service)

        # Clean up
        for cert, _ in new_certs:
            restart_file_handle.write('rm -f {cert}'.format(
                cert=cert,
            ))
        #restart_file_handle.write('rm %s\n' % restart_file_path)
    subprocess.call([
        'sudo',
        'at',
        'now', '+', '1', 'minutes',
        '-f', restart_file_path,
    ])


def regenerate_broker_certificates(subjectaltnames):
    subjectaltnames = get_general_subjectaltnames(subjectaltnames)

    private_cert_path = '/etc/rabbitmq/rabbit-priv.pem'
    public_cert_path = '/etc/rabbitmq/rabbit-pub.pem'
    tmp_private = os.path.join(MANAGER_TMP_PATH, 'broker-private')
    tmp_public = os.path.join(MANAGER_TMP_PATH, 'broker-public')
    build_certs(
        private_key_path=tmp_private,
        public_key_path=tmp_public,
        subjectaltnames=subjectaltnames,
    )

    new_certs = [
        (tmp_private, private_cert_path),
        (tmp_public, public_cert_path),
    ]

    for cert_path in [
        '/opt/manager/amqp_pub.pem',
        '/opt/mgmtworker/amqp_pub.pem',
        '/opt/amqpinflux/amqp_pub.pem',
    ]:
        new_certs.append((tmp_public, cert_path))

    services_to_restart = [
        # Rabbit
        'rabbitmq',
        # amqpinflux
        'cloudify-amqpinflux',
        # mgmtworker
        'cloudify-mgmtworker',
        # manager
        'cloudify-restservice',
    ]
    return services_to_restart, new_certs


def main():
    os.mkdir(MANAGER_TMP_PATH, 0o700)
    # 0o is 2/3 compatible octal literal prefix

    regenerate_host_keys()

    authorize_user_ssh_key(inputs['user_ssh_key'])

    services_to_restart = []
    new_certs = []

    # TODO: This should only happen with security enabled...
    # ...and these should only be inputs in that case as well
    services, certs = regenerate_broker_certificates(
        inputs['broker_names_and_ips']
    )
    services_to_restart.extend(services)
    new_certs.extend(new_certs)
    services, certs = regenerate_manager_certificates(
        inputs['manager_names_and_ips']
    )
    services_to_restart.extend(services)
    new_certs.extend(new_certs)

    with open('/tmp/cloudify_ssl_certificate_replacement.json', 'w') as fh:
        fh.write(json.dumps({
            'execution_id': ctx.execution_id,
            # Get these from somewhere, somehow...
            'cloudify_username': 'cloudify',
            'cloudify_password': 'cloudify',
            'new_certs': new_certs,
            'restart_services': services_to_restart,
        }))
    # Allow some time in case this is the last part of the workflow so that
    # the background cron job picks up and completes this at about the same
    # time that the workflow appears to finish for the user (hopefully)
    time.sleep(60)

if __name__ == '__main__':
    main()
