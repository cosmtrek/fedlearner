# Copyright 2020 The FedLearner Authors. All Rights Reserved.
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

# coding: utf-8
import io
import json
import re
from http import HTTPStatus
import logging
import tarfile

from flask import send_file
from flask_restful import Resource, reqparse, request
from google.protobuf.json_format import ParseDict, ParseError
from fedlearner_webconsole.workflow_template.models import WorkflowTemplate
from fedlearner_webconsole.proto import workflow_definition_pb2
from fedlearner_webconsole.db import db
from fedlearner_webconsole.exceptions import (
    NotFoundException, InvalidArgumentException,
    ResourceConflictException)


def _classify_variable(variable):
    if variable.value_type == 'CODE':
        try:
            json.loads(variable.value)
        except json.JSONDecodeError as e:
            raise InvalidArgumentException(str(e))
    return variable


def dict_to_workflow_definition(config):
    try:
        template_proto = ParseDict(config,
                                   workflow_definition_pb2.WorkflowDefinition())
        for variable in template_proto.variables:
            _classify_variable(variable)
        for job in template_proto.job_definitions:
            for variable in job.variables:
                _classify_variable(variable)
    except ParseError as e:
        raise InvalidArgumentException(details={'config': str(e)})
    return template_proto


def _dic_without_key(d, key):
    result = dict(d)
    del result[key]
    return result


class WorkflowTemplatesApi(Resource):
    def get(self):
        templates = WorkflowTemplate.query
        if 'group_alias' in request.args:
            templates = templates.filter_by(
                group_alias=request.args['group_alias'])
        if 'is_left' in request.args:
            is_left = request.args.get(key='is_left', type=int)
            if is_left is None:
                raise InvalidArgumentException('is_left must be 0 or 1')
            templates = templates.filter_by(is_left=is_left)
        # remove config from dicts to reduce the size of the list
        return {'data': [_dic_without_key(t.to_dict(),
                                          'config') for t in templates.all()
                         ]}, HTTPStatus.OK

    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('name', required=True, help='name is empty')
        parser.add_argument('comment')
        parser.add_argument('config', type=dict, required=True,
                            help='config is empty')
        data = parser.parse_args()
        name = data['name']
        comment = data['comment']
        config = data['config']
        if WorkflowTemplate.query.filter_by(name=name).first() is not None:
            raise ResourceConflictException(
                'Workflow template {} already exists'.format(name))
        template_proto = _check_config(config)
        template = WorkflowTemplate(name=name,
                                    comment=comment,
                                    group_alias=template_proto.group_alias,
                                    is_left=template_proto.is_left)
        template.set_config(template_proto)
        db.session.add(template)
        db.session.commit()
        logging.info('Inserted a workflow_template to db')
        return {'data': template.to_dict()}, HTTPStatus.CREATED


class WorkflowTemplateApi(Resource):
    def get(self, template_id):
        download = request.args.get('download', 'false') == 'true'

        template = WorkflowTemplate.query.filter_by(id=template_id).first()
        if template is None:
            raise NotFoundException()
        if download:
            in_memory_file = io.BytesIO()
            in_memory_file.write(json.dumps(template.to_dict()).encode('utf-8'))
            in_memory_file.seek(0)
            return send_file(in_memory_file,
                             as_attachment=True,
                             attachment_filename=f'{template.name}.json',
                             mimetype='application/json; charset=UTF-8',
                             cache_timeout=0)
        return {'data': template.to_dict()}, HTTPStatus.OK

    def delete(self, template_id):
        result = WorkflowTemplate.query.filter_by(id=template_id)
        if result.first() is None:
            raise NotFoundException()
        result.delete()
        db.session.commit()
        return {'data': {}}, HTTPStatus.OK

    def put(self, template_id):
        parser = reqparse.RequestParser()
        parser.add_argument('name', required=True, help='name is empty')
        parser.add_argument('comment')
        parser.add_argument('config', type=dict, required=True,
                            help='config is empty')
        data = parser.parse_args()
        name = data['name']
        comment = data['comment']
        config = data['config']
        tmp = WorkflowTemplate.query.filter_by(name=name).first()
        if tmp is not None and tmp.id != template_id:
            raise ResourceConflictException(
                'Workflow template {} already exists'.format(name))
        template = WorkflowTemplate.query.filter_by(id=template_id).first()
        if template is None:
            raise NotFoundException()
        template_proto = _check_config(config)
        template.set_config(template_proto)
        template.name = name
        template.comment = comment
        template.group_alias = template_proto.group_alias
        template.is_left = template_proto.is_left
        db.session.commit()
        return {'data': template.to_dict()}, HTTPStatus.OK


def _check_config(config):
    # TODO: needs tests
    if 'group_alias' not in config:
        raise InvalidArgumentException(details={
            'config.group_alias': 'config.group_alias is required'})
    if 'is_left' not in config:
        raise InvalidArgumentException(
            details={'config.is_left': 'config.is_left is required'})

    # form to proto buffer
    template_proto = dict_to_workflow_definition(config)
    for index, job_def in enumerate(template_proto.job_definitions):
        # pod label name must be no more than 63 characters.
        #  workflow.uuid is 20 characters, pod name suffix such as
        #  '-follower-master-0' is less than 19 characters, so the
        #  job name must be no more than 24
        if len(job_def.name) > 24:
            raise InvalidArgumentException(
                details=
                {f'config.job_definitions[{index}].job_name'
                 : 'job_name must be no more than 24 characters'})
        # limit from k8s
        if not re.match('[a-z0-9-]*', job_def.name):
            raise InvalidArgumentException(
                details=
                {f'config.job_definitions[{index}].job_name'
                 : 'Only letters(a-z), numbers(0-9) '
                   'and dashes(-) are supported.'})
    return template_proto


class CodeApi(Resource):
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument('code_path', type=str, location='args',
                            required=True,
                            help='code_path is required')
        data = parser.parse_args()
        code_path = data['code_path']
        try:
            with tarfile.open(code_path) as tar:
                code_dict = {}
                for file in tar.getmembers():
                    if tar.extractfile(file) is not None:
                        if '._' not in file.name and file.isfile():
                            code_dict[file.name] = str(
                                tar.extractfile(file).read(),
                                encoding='utf-8')
                return {'data': code_dict}, HTTPStatus.OK
        except Exception as e:
            logging.error('Get code: %s', repr(e))
            raise InvalidArgumentException(details={'code_path': 'wrong path'})


def initialize_workflow_template_apis(api):
    api.add_resource(WorkflowTemplatesApi, '/workflow_templates')
    api.add_resource(WorkflowTemplateApi,
                     '/workflow_templates/<int:template_id>')
    api.add_resource(CodeApi, '/codes')
