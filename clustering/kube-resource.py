#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# (c) 2016, Urs Breu <dev@gleisdrei.ch>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = """
module: kube-resource
short_description: Manage kubernetes resources.
description:
  - Ansible version of the "kubectl" CLI command.
  - This module allows you to create, delete and update kubernetes resources,
    such as replication controllers, services, endpoints, namespaces and secrets.
  - See U(http://kubernetes.io/) and U(http://kubernetes.io/v1.1/docs/user-guide/kubectl-overview.html)
author: Urs Breu (@ubreu)
version_added: "2.1"
options:
  src:
    required: false
    description:
      - The remote path of the file describing the resource.
  name:
    required: true
    description:
      - The name of the resource. In the case of a replication controller this
        is the value of the k8s-app label assigned to the managed pod.
  type:
    required: true
    choices: [ rc, svc, secret, endpoints, namespace ]
    description:
      - The type of the resource to be created/updated or deleted.
  namespace:
    default: "default"
    description:
      - The name of the kubernetes namespace to use. Defaults to the I(default) namespace.
  state:
    required: true
    choices: [ created, deleted, recreated, updated ]
    default: "created"
    description:
      - C(created) will create a resource if it is not already present.
        C(deleted) will delete a resource if it is present.
        C(recreated) will first delete an existing resource if it exists and it will create it.
        C(updated) will perform a rolling update. This only works for replication controllers.
"""

EXAMPLES = """
# Create a kubernetes service in the default namespace
- kube-resource: src=/etc/kubernetes/my-service.yml name=my-service type=svc state=created
# Create a kubernetes service in the prod namespace
- kube-resource: src=/etc/kubernetes/my-service.yml name=my-service type=svc state=created namespace=prod
# Update a kubernetes service in the default namespace
- kube-resource: src=/etc/kubernetes/my-service.yml name=my-service type=svc state=updated
# Delete and recreate an existing kubernetes service in the default namespace
- kube-resource: src=/etc/kubernetes/my-service.yml name=my-service type=svc state=recreated
# Delete a kubernetes service in the default namespace
- kube-resource: name=my-service type=svc state=deleted
"""

RETURN = """
stdout:
    description: standard output of kubectl command
    returned: when supported by kubectl command
    type: string
    sample: "secret 'my-secret' created"
stderr:
    description: standard error of kubectl command
    returned: when supported by kubectl command
    type: string
    sample: "Failed to create service 'my-service'"
msg:
    description: message describing the action that would be performed
    returned: when run in check mode
    type: string
    sample: "perform rolling update of rc with label 'my-app' in namespace 'default' using '/etc/kubernetes/apps/my-app-rc.yml'"
"""

import json
import subprocess
import re

def get_cmd(module):
    return [module.get_bin_path('kubectl', True)]

def add_selector(cmd, resource_type, name):
    if resource_type == 'rc':
      cmd.append('-l k8s-app=' + name)
    else:
      cmd.append(name)
    return

def is_present(module, resource_type, name, namespace):
    cmd = get_cmd(module)
    cmd.append('get')
    cmd.append(resource_type)
    add_selector(cmd, resource_type, name)
    cmd.append('-o')
    cmd.append('json')
    cmd.append('--namespace=' + namespace)
    (rc, out, err) = module.run_command(cmd)
    ok = rc is not None and rc == 0
    if ok:
      response = json.loads(out)
      present = len(response) != 0
      if "items" in response:
        present = len(response['items']) != 0
    else:
      present = False
      response = None

    return present, response

def delete(module, resource_type, name, namespace):
    if module.check_mode: log_change_and_exit(module, "deleting %s '%s' in namespace '%s'" % (resource_type, name, namespace))

    cmd = get_cmd(module)
    cmd.append('delete')
    cmd.append(resource_type)
    add_selector(cmd, resource_type, name)
    cmd.append('--namespace=' + namespace)
    return module.run_command(cmd)

def create(module, src, namespace):
    if module.check_mode: log_change_and_exit(module, "creating resource in namespace '%s' using '%s'" % (namespace, src))

    cmd = get_cmd(module)
    cmd.append('create')
    cmd.append('-f')
    cmd.append(src)
    cmd.append('--namespace=' + namespace)
    return module.run_command(cmd)

def rolling_update(module, src, data, namespace):
    name = data['items'][0]['metadata']['name']
    if module.check_mode: log_change_and_exit(module, "perform rolling update of rc with label '%s' in namespace '%s' using '%s'" % (name, namespace, src))

    cmd = get_cmd(module)
    cmd.append('rolling-update')
    cmd.append(name)
    cmd.append('-f')
    cmd.append(src)
    cmd.append('--namespace=' + namespace)
    return module.run_command(cmd)

def log_change_and_exit(module, msg):
    if module.check_mode:
        result = {}
        result['changed'] = True
        result['msg'] = msg
        module.exit_json(**result)

def main():
    module = AnsibleModule(
        supports_check_mode=True,
        argument_spec = dict(
            src=dict(required=True),
            name=dict(required=True),
            namespace=dict(default='default'),
            type=dict(required=True, choices= [ 'rc', 'svc', 'secret', 'endpoints', 'namespace' ]),
            state=dict(choices=['created', 'deleted', 'recreated', 'updated'], default='created'),
        ),
    )

    src = module.params['src']
    name = module.params['name']
    namespace = module.params['namespace']
    resource_type = module.params['type']
    state = module.params['state']

    present, data = is_present(module, resource_type, name, namespace)
    changed = True

    if state == 'created' and not present:
        (rc, out, err) = create(module, src, namespace)
    elif state == 'deleted' and present:
        (rc, out, err) = delete(module, resource_type, name, namespace)
    elif state == 'recreated':
        if present:
            (rc, out, err) = delete(module, resource_type, name, namespace)
            if rc is not None and rc != 0:
                module.fail_json(name=name, msg=err, rc=rc)

        (rc, out, err) = create(module, src, namespace)
    elif state == 'updated':
        if present:
            (rc, out, err) = rolling_update(module, src, data, namespace)
        else:
            (rc, out, err) = create(module, src, namespace)
    else:
        rc = 0
        changed = False
        err = ""
        out = ""

    if rc is not None and rc != 0:
      module.fail_json(name=name, msg=err, rc=rc)

    result = {}
    result['stdout'] = out
    result['stderr'] = err
    result['changed'] = changed

    module.exit_json(**result)
from ansible.module_utils.basic import *
main()
