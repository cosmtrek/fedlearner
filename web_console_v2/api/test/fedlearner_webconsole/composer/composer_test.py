# Copyright 2021 The FedLearner Authors. All Rights Reserved.
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
# pylint: disable=broad-except

import logging
import sys
import threading
import time
import unittest

from testing.common import BaseTestCase

from fedlearner_webconsole.composer.composer import Composer, ComposerOption
from fedlearner_webconsole.composer.models import RunnerStatus, SchedulerItem, SchedulerRunner
from fedlearner_webconsole.db import db
from fedlearner_webconsole.composer.interface import IItem, IRunner, ItemType


class Task(IItem):
    def __init__(self, task_id: int):
        self.id = task_id

    def type(self) -> ItemType:
        return ItemType.TASK

    def get_id(self) -> int:
        return self.id


class TaskRunner(IRunner):
    def __init__(self, task_id: int):
        self.task_id = task_id

    def start(self, context: dict):
        logging.info(f"[mock_task_runner] started, ctx: {context}")

    def stop(self, context: dict):
        logging.info(f"[mock_task_runner] stopped, ctx: {context}")

    def timeout(self) -> int:
        return 60

    def is_done(self) -> bool:
        time.sleep(2)
        return True

    def output(self) -> dict:
        return {'output': f'task_{self.task_id} game over'}

    def is_failed(self) -> bool:
        return False


class ComposerTest(BaseTestCase):
    runner_fn = {
        ItemType.TASK.value: TaskRunner,
    }
    option = ComposerOption(engine=db.engine,
                            runner_fn=runner_fn,
                            option={'name': 'next scheduler'})

    class Config(BaseTestCase.Config):
        STORAGE_ROOT = '/tmp'
        START_SCHEDULER = False
        START_GRPC_SERVER = False

    def setUp(self):
        super().setUp()

    def test_normal_items(self):
        composer = Composer(option=self.option)
        items = [Task(1), Task(2), Task(3)]
        composer.collect('test_collect', items, {})
        self.assertEqual(1, len(db.session.query(SchedulerItem).all()),
                         'incorrect items')

        cr = threading.Thread(target=composer.run)
        cr.start()
        time.sleep(30)
        cs = threading.Thread(target=composer.stop, daemon=True)
        cs.start()
        cr.join()
        cs.join()
        self.assertEqual(1, len(db.session.query(SchedulerRunner).all()),
                         'incorrect runners')
        self.assertEqual(RunnerStatus.DONE.value,
                         db.session.query(SchedulerRunner).first().status,
                         'should finish it')

    def test_failed_items(self):
        pass


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    unittest.main()
