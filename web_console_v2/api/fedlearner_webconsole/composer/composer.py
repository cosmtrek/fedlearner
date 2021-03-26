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

import json
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy.engine import Engine
from sqlalchemy.orm import scoped_session
from sqlalchemy.orm import sessionmaker

from fedlearner_webconsole.composer.interface import IItem, IRunner
from fedlearner_webconsole.composer.models import SchedulerItem, ItemStatus, SchedulerRunner, RunnerStatus


class ComposerOption(object):
    def __init__(self, engine: Engine, runner_fn: dict, option: dict):
        """Option for composer

        Args:
            engine: database engine
            runner_fn: runner functions
            option: other config options
        """
        self.engine = engine
        self.runner_fn = runner_fn
        self.option = option


class Pipeline(object):
    def __init__(self):
        """Define the deps of scheduler item

        Fields:
             name: pipeline name
             deps: items to be processed in order
             meta: additional info
        """
        self.name = ''
        self.deps = []
        self.meta = {}

    def build(self, name: str, deps: [str], meta: dict):
        self.name = name
        self.deps = deps
        self.meta = meta

    def from_json(self, val: str) -> 'Pipeline':
        obj = json.loads(val)
        self.name = obj['name']
        self.deps = obj['deps']
        self.meta = obj['meta']
        return self

    def to_json(self) -> str:
        return json.dumps(self, default=lambda o: o.__dict__)


class Composer(object):
    def __init__(self, option: ComposerOption):
        session_factory = sessionmaker(bind=option.engine)
        self.session = scoped_session(session_factory)

        self.lock = threading.Lock()
        # make the task be runner
        self.planner = Planner(self.session, self.lock, option.option)
        # run the task
        self.worker = Worker(self.session, self.lock, option.runner_fn,
                             option.option)

    def run(self):
        logging.info('[composer] starting...')
        planner = threading.Thread(target=self.planner.run,
                                   args=[],
                                   daemon=True)
        planner.start()
        worker = threading.Thread(target=self.worker.run, args=[], daemon=True)
        worker.start()
        planner.join()
        worker.join()

    def stop(self):
        logging.info('[composer] stopping...')
        self.planner.stop()
        self.worker.stop()

    # collect items
    def collect(self, name: str, items: [IItem], options: dict):
        conn = self.session()
        item = SchedulerItem(
            name=name,
            pipeline=self.__build_pipeline(name, items, options).to_json(),
        )
        conn.add(item)
        try:
            conn.commit()
        except Exception as e:
            logging.error(
                f'[composer] failed to create scheduler_item, name: {name} exception: {e}'
            )
            conn.rollback()

        self.session.remove()

    @staticmethod
    def __build_pipeline(name: str, items: [IItem], options: dict) -> Pipeline:
        p = Pipeline()
        deps = []
        for idx, item in enumerate(items):
            deps.append(f'{item.type().value}_{item.get_id()}')
        p.build(name, deps, options)
        return p


class Planner(object):
    def __init__(self, session: scoped_session, lock: threading.Lock,
                 option: dict):
        self.session = session
        self.lock = lock
        self.option = option
        self.__stop = False

    def run(self):
        logging.info('[composer] start planner')
        while True:
            with self.lock:
                if self.__stop:
                    logging.info('[composer] stop planner')
                    return
            logging.info('[composer] checking scheduler items')
            conn = self.session()
            items = conn.query(SchedulerItem).filter_by(
                status=ItemStatus.ON.value).all()
            for item in items:
                if item.need_run():
                    item.last_run_at = datetime.now()
                    runner = SchedulerRunner(
                        item_id=item.id,
                        pipeline=item.pipeline,
                    )
                    conn.add(runner)
                    try:
                        logging.info(
                            f'[composer] insert new runner, item_id: {item.id}, runner_id: {runner.id}'
                        )
                        conn.commit()
                    except Exception as e:
                        logging.error(
                            f'[composer] failed to create scheduler_runner, item_id: {item.id}, exception: {e}'
                        )
                        conn.rollback()

            self.session.remove()
            time.sleep(5)

    def stop(self):
        with self.lock:
            self.__stop = True


class Worker(object):
    def __init__(self, session: scoped_session, lock: threading.Lock,
                 runner_fn: dict, option: dict):
        self.session = session
        self.lock = lock
        self.runner_fn = runner_fn
        self.__stop = False
        worker_num = option.get('worker_num', 10)
        # TODO: thread pool with timeout
        self.thread_pool = ThreadPoolExecutor(max_workers=worker_num)

    def run(self):
        logging.info('[composer] start worker')
        while True:
            with self.lock:
                if self.__stop:
                    logging.info('[composer] stop worker')
                    self.thread_pool.shutdown(wait=True)
                    return
            logging.info('[composer] checking scheduler runners')
            self.__check_init_runners()
            self.__check_running_runners()
            time.sleep(5)

    def stop(self):
        with self.lock:
            self.__stop = True

    def __check_init_runners(self):
        conn = self.session()
        init_runners = conn.query(SchedulerRunner).filter_by(
            status=RunnerStatus.INIT.value).all()
        for runner in init_runners:
            pipeline = Pipeline().from_json(runner.pipeline)
            # find the first job in pipeline
            first = pipeline.deps[0]
            # update status
            runner.start_at = datetime.now()
            runner.status = RunnerStatus.RUNNING.value
            output = json.loads(runner.output)
            output[first] = {'status': RunnerStatus.RUNNING.value}
            runner.output = json.dumps(output)
            context = json.loads(runner.context)
            context['current'] = first  # record current running job
            context['current_idx'] = 0
            context['remaining'] = len(pipeline.deps) - 1
            runner.context = json.dumps(context)
            # start runner
            runner_fn = self.__find_runner_fn(first)
            logging.info(f'start runner {runner.id}')
            self.thread_pool.submit(lambda x: runner_fn.start(x),
                                    json.loads(runner.context))
            try:
                conn.commit()
            except Exception as e:
                logging.error(
                    f'[composer] failed to update runner status, exception: {e}'
                )
            finally:
                conn.rollback()
        self.session.remove()

    def __check_running_runners(self):
        conn = self.session()
        running_runners = conn.query(SchedulerRunner).filter_by(
            status=RunnerStatus.RUNNING.value).all()
        for runner in running_runners:
            pipeline = Pipeline().from_json(runner.pipeline)
            output = json.loads(runner.output)
            context = json.loads(runner.context)
            current = context['current']
            runner_fn = self.__find_runner_fn(current)
            # check status of current one
            if runner_fn.is_done():
                output[current] = {'status': RunnerStatus.DONE.value}
                context[current] = runner_fn.output()
                context['current_idx'] = context['current_idx'] + 1
                if context['remaining'] == 0:  # all done
                    runner.status = RunnerStatus.DONE.value
                    runner.end_at = datetime.now()
                else:  # run next one
                    next_idx = context['current_idx']
                    next_one = pipeline.deps[next_idx]
                    output[next_one] = {'status': RunnerStatus.RUNNING.value}
                    context['current'] = next_one
                    context['remaining'] = context['remaining'] - 1
                    next_runner_fn = self.__find_runner_fn(next_one)
                    self.thread_pool.submit(lambda x: next_runner_fn.start(x),
                                            context)
            elif runner_fn.is_failed():
                # TODO: abort now, need retry
                output[current] = {'status': RunnerStatus.FAILED.value}
                context[current] = runner_fn.output()
                runner.status = RunnerStatus.FAILED.value
                runner.end_at = datetime.now()

            logging.info(
                f'[composer] update runner, pipeline: {pipeline}, output: {output}, context: {context}'
            )
            runner.pipeline = pipeline.to_json()
            runner.output = json.dumps(output)
            runner.context = json.dumps(context)
            conn.commit()
        self.session.remove()

    def __find_runner_fn(self, runner_id: str) -> IRunner:
        item_type, item_id = runner_id.split('_')
        return self.runner_fn[item_type](item_id)
