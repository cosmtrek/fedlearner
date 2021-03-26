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

import enum
from datetime import datetime, timedelta

from sqlalchemy.sql import func

from fedlearner_webconsole.utils import db_table
from fedlearner_webconsole.db import db, to_dict_mixin


class ItemStatus(enum.Enum):
    OFF = 0
    ON = 1


@to_dict_mixin()
class SchedulerItem(db.Model):
    __tablename__ = 'scheduler_item_v2'
    __table_args__ = (db_table.default_table_args('scheduler items'))
    id = db.Column(db.Integer,
                   comment='id',
                   primary_key=True,
                   autoincrement=True)
    name = db.Column(db.String(255), comment='item name', nullable=False)
    pipeline = db.Column(db.Text, comment='pipeline', nullable=False)
    status = db.Column(db.Integer,
                       comment='item status',
                       nullable=False,
                       default=ItemStatus.ON.value)
    interval = db.Column(db.Integer,
                         comment='item interval',
                         nullable=False,
                         default=-1)
    last_run_at = db.Column(db.DateTime(timezone=True),
                            comment='last runner time')
    retry_cnt = db.Column(db.Integer,
                          comment='retry count when item is failed',
                          nullable=False,
                          default=0)
    extra = db.Column(db.Text(), comment='extra info')
    created_at = db.Column(db.DateTime(timezone=True),
                           comment='created at',
                           server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True),
                           comment='updated at',
                           server_default=func.now(),
                           onupdate=func.now())
    deleted_at = db.Column(db.DateTime(timezone=True), comment='deleted at')

    def need_run(self) -> bool:
        if self.interval == -1 and self.last_run_at is None:
            return True
        elif self.interval > 0:  # cronjob
            if self.last_run_at is None:  # never run
                return True
            elif self.last_run_at + timedelta(
                    seconds=self.interval) < datetime.now():
                return True
        return False


class RunnerStatus(enum.Enum):
    INIT = 0
    RUNNING = 1
    DONE = 2
    FAILED = 3


@to_dict_mixin()
class SchedulerRunner(db.Model):
    __tablename__ = 'scheduler_runner_v2'
    __table_args__ = (db_table.default_table_args('scheduler runners'))
    id = db.Column(db.Integer,
                   comment='id',
                   primary_key=True,
                   autoincrement=True)
    item_id = db.Column(db.Integer, comment='item id', nullable=False)
    status = db.Column(db.Integer,
                       comment='runner status',
                       nullable=False,
                       default=RunnerStatus.INIT.value)
    start_at = db.Column(db.DateTime(timezone=True),
                         comment='runner start time')
    end_at = db.Column(db.DateTime(timezone=True), comment='runner end time')
    pipeline = db.Column(db.Text(),
                         comment='pipeline from scheduler item',
                         nullable=False,
                         default='{}')
    output = db.Column(db.Text(),
                       comment='output',
                       nullable=False,
                       default='{}')
    context = db.Column(db.Text(),
                        comment='context',
                        nullable=False,
                        default='{}')
    extra = db.Column(db.Text(), comment='extra info')
    created_at = db.Column(db.DateTime(timezone=True),
                           comment='created at',
                           server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True),
                           comment='updated at',
                           server_default=func.now(),
                           onupdate=func.now())
    deleted_at = db.Column(db.DateTime(timezone=True), comment='deleted at')
