# Copyright 2021, 2022 IBM Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import enum
import functools
import logging
import pathlib
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    cast,
)

import graphviz
import numpy as np
import pandas as pd
import sklearn.model_selection
import sklearn.tree

import lale.helpers
import lale.json_operator
import lale.pretty_print
from lale.datasets import pandas2spark
from lale.operators import (
    TrainableIndividualOp,
    TrainablePipeline,
    TrainedIndividualOp,
    TrainedPipeline,
)

from .metrics import MetricMonoid, MetricMonoidFactory
from .monoid import Monoid, MonoidFactory

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

_BatchStatus = enum.Enum("BatchStatus", "RESIDENT SPILLED")

_TaskStatus = enum.Enum("_TaskStatus", "FRESH READY WAITING DONE")

_Operation = enum.Enum(
    "_Operation", "SCAN TRANSFORM PREDICT FIT PARTIAL_FIT TO_MONOID COMBINE"
)

_DUMMY_INPUT_STEP = -1

_DUMMY_SCORE_STEP = sys.maxsize


def is_pretrained(op: TrainableIndividualOp) -> bool:
    return isinstance(op, TrainedIndividualOp) and (
        op.is_frozen_trained() or not hasattr(op.impl, "fit")
    )


def is_incremental(op: TrainableIndividualOp) -> bool:
    return op.has_method("partial_fit") or is_pretrained(op)


def is_associative(op: TrainableIndividualOp) -> bool:
    return is_pretrained(op) or isinstance(op.impl, MonoidFactory)


def _batch_id(fold: str, idx: int) -> str:
    return fold + str(idx)


def _get_fold(batch_id: str) -> str:
    return batch_id[0]


def _get_idx(batch_id: str) -> int:
    return int(batch_id[1:])


_MemoKey = Tuple[Type["_Task"], int, Tuple[str, ...], Optional[str]]


class _Task:
    preds: List["_Task"]
    succs: List["_Task"]

    def __init__(
        self, step_id: int, batch_ids: Tuple[str, ...], held_out: Optional[str]
    ):
        self.step_id = step_id
        self.batch_ids = batch_ids
        self.held_out = held_out
        self.status = _TaskStatus.FRESH
        self.preds = []
        self.succs = []
        self.deletable_output = True

    @abstractmethod
    def get_operation(
        self, pipeline: TrainablePipeline[TrainableIndividualOp]
    ) -> _Operation:
        pass

    def memo_key(self) -> _MemoKey:
        return type(self), self.step_id, self.batch_ids, self.held_out


class _TrainTask(_Task):
    monoid: Optional[Monoid]
    trained: Optional[TrainedIndividualOp]

    def __init__(self, step_id: int, batch_ids: Tuple[str, ...], held_out: str):
        super().__init__(step_id, batch_ids, held_out)
        self.monoid = None
        self.trained = None

    def get_operation(
        self, pipeline: TrainablePipeline[TrainableIndividualOp]
    ) -> _Operation:
        any_pred_train = any(isinstance(p, _TrainTask) for p in self.preds)
        any_succ_train = any(isinstance(s, _TrainTask) for s in self.succs)
        if not any_pred_train and not any_succ_train:
            return _Operation.FIT
        step = pipeline.steps_list()[self.step_id]
        if is_associative(step):
            if len(self.batch_ids) == 1:
                return _Operation.TO_MONOID
            return _Operation.COMBINE
        return _Operation.PARTIAL_FIT

    def get_trained(
        self, pipeline: TrainablePipeline[TrainableIndividualOp]
    ) -> TrainedIndividualOp:
        if self.trained is None:
            assert self.monoid is not None
            trainable = pipeline.steps_list()[self.step_id]
            self.trained = trainable.convert_to_trained()
            hyperparams = trainable.impl._hyperparams
            self.trained._impl = trainable._impl_class()(**hyperparams)
            if trainable.has_method("_set_fit_attributes"):
                self.trained._impl._set_fit_attributes(self.monoid)
            elif trainable.has_method("from_monoid"):
                self.trained._impl.from_monoid(self.monoid)
            else:
                assert False, self.trained
        return self.trained


if lale.helpers.spark_installed:
    from pyspark.sql.dataframe import DataFrame as SparkDataFrame

    _DataFrame = Union[pd.DataFrame, SparkDataFrame]
    _Series = Union[pd.Series, SparkDataFrame]
    _RawBatch = Union[
        Tuple[pd.DataFrame, pd.Series],
        Tuple[SparkDataFrame, SparkDataFrame],
    ]
else:
    _DataFrame = pd.DataFrame  # type: ignore
    _Series = pd.Series  # type: ignore
    _RawBatch = Tuple[pd.DataFrame, pd.Series]  # type: ignore


class _Batch:
    X: Union[_DataFrame, pathlib.Path]
    y: Union[_Series, pathlib.Path]

    def __init__(self, X: _DataFrame, y: _Series, task: Optional["_ApplyTask"]):
        self.X = X
        self.y = y
        self.task = task
        if isinstance(X, pd.DataFrame) and isinstance(y, pd.Series):
            space_X = int(cast(pd.DataFrame, X).memory_usage().sum())
            space_y = cast(pd.Series, y).memory_usage()
            self.space = space_X + space_y
        else:
            self.space = 1  # place-holder value for Spark

    def spill(self, spill_dir: pathlib.Path) -> None:
        name_X = spill_dir / f"X_{self}.pkl"
        name_y = spill_dir / f"y_{self}.pkl"
        if isinstance(self.X, pd.DataFrame):
            cast(pd.DataFrame, self.X).to_pickle(name_X)
        elif isinstance(self.X, np.ndarray):
            np.save(name_X, self.X, allow_pickle=True)
        else:
            raise ValueError(
                f"""Spilling of {type(self.X)} is not supported.
            Supported types are: pandas DataFrame, numpy ndarray."""
            )
        if isinstance(self.y, pd.Series):
            cast(pd.Series, self.y).to_pickle(name_y)
        elif isinstance(self.y, np.ndarray):
            np.save(name_y, self.y, allow_pickle=True)
        else:
            raise ValueError(
                f"""Spilling of {type(self.y)} is not supported.
            Supported types are: pandas DataFrame, pandas Series, and numpy ndarray."""
            )
        self.X, self.y = name_X, name_y

    def load_spilled(self) -> None:
        assert isinstance(self.X, pathlib.Path) and isinstance(self.y, pathlib.Path)
        try:
            data_X = pd.read_pickle(self.X)
        except FileNotFoundError:
            data_X = np.load(f"{self.X}" + ".npy", allow_pickle=True)
        try:
            data_y = pd.read_pickle(self.y)
        except FileNotFoundError:
            data_y = np.load(f"{self.y}" + ".npy", allow_pickle=True)
        self.X, self.y = data_X, data_y

    def delete_if_spilled(self) -> None:
        if isinstance(self.X, pathlib.Path) and isinstance(self.y, pathlib.Path):
            self.X.unlink()
            self.y.unlink()

    def __str__(self) -> str:
        assert self.task is not None and len(self.task.batch_ids) == 1
        batch_id = self.task.batch_ids[0]
        return f"{self.task.step_id}_{batch_id}_{self.task.held_out}"

    @property
    def Xy(self) -> _RawBatch:
        assert self.status == _BatchStatus.RESIDENT
        return self.X, self.y

    @property
    def status(self) -> _BatchStatus:
        if isinstance(self.X, pd.DataFrame) and isinstance(self.y, pd.Series):
            return _BatchStatus.RESIDENT
        if isinstance(self.X, SparkDataFrame) and isinstance(self.y, SparkDataFrame):
            return _BatchStatus.RESIDENT
        if isinstance(self.X, pathlib.Path) and isinstance(self.y, pathlib.Path):
            return _BatchStatus.SPILLED
        if isinstance(self.X, np.ndarray) and isinstance(self.y, pd.Series):
            return _BatchStatus.RESIDENT
        if isinstance(self.X, np.ndarray) and isinstance(self.y, np.ndarray):
            return _BatchStatus.RESIDENT
        assert False, (type(self.X), type(self.y))


class _ApplyTask(_Task):
    batch: Optional[_Batch]

    def __init__(self, step_id: int, batch_ids: Tuple[str, ...], held_out: str):
        super().__init__(step_id, batch_ids, held_out)
        self.batch = None

    def get_operation(self, pipeline: TrainablePipeline) -> _Operation:
        if self.step_id == _DUMMY_INPUT_STEP:
            return _Operation.SCAN
        step = pipeline.steps_list()[self.step_id]
        return _Operation.TRANSFORM if step.is_transformer() else _Operation.PREDICT


class _MetricTask(_Task):
    mscore: Optional[MetricMonoid]

    def __init__(self, step_id: int, batch_ids: Tuple[str, ...], held_out: str):
        super().__init__(step_id, batch_ids, held_out)
        self.mmonoid = None

    def get_operation(self, pipeline: TrainablePipeline) -> _Operation:
        if len(self.batch_ids) == 1:
            return _Operation.TO_MONOID
        return _Operation.COMBINE


class Prio(ABC):
    arity: int

    def bottom(self) -> Any:  # tuple of "inf" means all others are more important
        return self.arity * (float("inf"),)

    def batch_priority(self, batch: _Batch) -> Any:  # prefer to keep resident if lower
        assert batch.task is not None
        return min(
            (
                self.task_priority(s)
                for s in batch.task.succs
                if s.status in [_TaskStatus.READY, _TaskStatus.WAITING]
            ),
            default=self.bottom(),
        )

    @abstractmethod
    def task_priority(self, task: _Task) -> Any:  # prefer to do first if lower
        pass


class PrioStep(Prio):
    arity = 5

    def task_priority(self, task: _Task) -> Any:
        result = (
            task.status.value,
            task.step_id,
            len(task.batch_ids),
            task.batch_ids,
            0 if isinstance(task, _TrainTask) else 1,
        )
        assert len(result) == self.arity
        return result


class PrioBatch(Prio):
    arity = 5

    def task_priority(self, task: _Task) -> Any:
        result = (
            task.status.value,
            len(task.batch_ids),
            task.batch_ids,
            task.step_id,
            0 if isinstance(task, _TrainTask) else 1,
        )
        assert len(result) == self.arity
        return result


class PrioResourceAware(Prio):
    arity = 5

    def task_priority(self, task: _Task) -> Any:
        non_res = sum(
            p.batch.space
            for p in task.preds
            if isinstance(p, _ApplyTask) and p.batch is not None
            if p.batch.status != _BatchStatus.RESIDENT
        )
        result = (
            task.status.value,
            non_res,
            task.batch_ids,
            task.step_id,
            0 if isinstance(task, _TrainTask) else 1,
        )
        assert len(result) == self.arity
        return result


def _step_id_to_string(
    step_id: int,
    pipeline: TrainablePipeline,
    cls2label: Dict[str, str] = {},
) -> str:
    if step_id == _DUMMY_INPUT_STEP:
        return "INP"
    if step_id == _DUMMY_SCORE_STEP:
        return "SCR"
    step = pipeline.steps_list()[step_id]
    cls = step.class_name()
    return cls2label[cls] if cls in cls2label else step.name()


def _task_to_string(
    task: _Task,
    pipeline: TrainablePipeline,
    cls2label: Dict[str, str] = {},
    sep: str = "\n",
    trace_id: int = None,
) -> str:
    trace_id_s = "" if trace_id is None else f"{trace_id} "
    operation_s = task.get_operation(pipeline).name.lower()
    step_s = _step_id_to_string(task.step_id, pipeline, cls2label)
    batches_s = ",".join(task.batch_ids)
    held_out_s = "" if task.held_out is None else f"#~{task.held_out}"
    return f"{trace_id_s}{operation_s}{sep}{step_s}({batches_s}){held_out_s}"


class _RunStats:
    _values: Dict[str, float]

    def __init__(self):
        object.__setattr__(
            self,
            "_values",
            {
                "spill_count": 0,
                "load_count": 0,
                "spill_space": 0,
                "load_space": 0,
                "min_resident": 0,
                "max_resident": 0,
                "train_count": 0,
                "apply_count": 0,
                "metric_count": 0,
                "train_time": 0,
                "apply_time": 0,
                "metric_time": 0,
                "critical_count": 0,
                "critical_time": 0,
            },
        )

    def __getattr__(self, name: str) -> float:
        if name in self._values:
            return self._values[name]
        raise AttributeError(f"'{name}' not in {self._values.keys()}")

    def __setattr__(self, name: str, value: float) -> None:
        if name in self._values:
            self._values[name] = value
        else:
            raise AttributeError(f"'{name}' not in {self._values.keys()}")

    def __repr__(self) -> str:
        return lale.pretty_print.json_to_string(self._values)


class _TraceRecord:
    def __init__(self, task, time):
        self.task = task
        self.time = time
        if isinstance(task, _ApplyTask):
            assert task.batch is not None
            self.space = task.batch.space
        else:
            self.space = 0  # TODO: size for train tasks and metrics tasks


def _visualize_tasks(
    tasks: Dict[_MemoKey, _Task],
    pipeline: TrainablePipeline[TrainableIndividualOp],
    prio: Prio,
    call_depth: int,
    trace: Optional[List[_TraceRecord]],
) -> None:
    cls2label = lale.json_operator._get_cls2label(call_depth + 1)
    dot = graphviz.Digraph()
    dot.attr("graph", rankdir="LR", nodesep="0.1")
    dot.attr("node", fontsize="11", margin="0.03,0.03", shape="box", height="0.1")
    next_task = min(tasks.values(), key=lambda t: prio.task_priority(t))
    task_key2trace_id: Dict[_MemoKey, int] = {}
    if trace is not None:
        task_key2trace_id = {r.task.memo_key(): i for i, r in enumerate(trace)}
    for task in tasks.values():
        if task.status is _TaskStatus.FRESH:
            color = "white"
        elif task.status is _TaskStatus.READY:
            color = "lightgreen" if task is next_task else "yellow"
        elif task.status is _TaskStatus.WAITING:
            color = "coral"
        else:
            assert task.status is _TaskStatus.DONE
            color = "lightgray"
        # https://www.graphviz.org/doc/info/shapes.html
        if isinstance(task, _TrainTask):
            style = "filled,rounded"
        elif isinstance(task, _ApplyTask):
            style = "filled"
        elif isinstance(task, _MetricTask):
            style = "filled,diagonals"
        else:
            assert False, type(task)
        trace_id = task_key2trace_id.get(task.memo_key(), None)
        task_s = _task_to_string(task, pipeline, cls2label, trace_id=trace_id)
        dot.node(task_s, style=style, fillcolor=color)
    for task in tasks.values():
        trace_id = task_key2trace_id.get(task.memo_key(), None)
        task_s = _task_to_string(task, pipeline, cls2label, trace_id=trace_id)
        for succ in task.succs:
            succ_id = task_key2trace_id.get(succ.memo_key(), None)
            succ_s = _task_to_string(succ, pipeline, cls2label, trace_id=succ_id)
            dot.edge(task_s, succ_s)

    import IPython.display

    IPython.display.display(dot)


class _TaskGraph:
    step_ids: Dict[TrainableIndividualOp, int]
    step_id_preds: Dict[int, List[int]]
    fresh_tasks: List[_Task]
    all_tasks: Dict[_MemoKey, _Task]

    def __init__(self, pipeline: TrainablePipeline[TrainableIndividualOp]):
        self.pipeline = pipeline
        self.step_ids = {step: i for i, step in enumerate(pipeline.steps_list())}
        self.step_id_preds = {
            self.step_ids[s]: (
                [_DUMMY_INPUT_STEP]
                if len(pipeline._preds[s]) == 0
                else [self.step_ids[p] for p in pipeline._preds[s]]
            )
            for s in pipeline.steps_list()
        }
        self.fresh_tasks = []
        self.all_tasks = {}

    def find_or_create(
        self,
        task_class: Type["_Task"],
        step_id: int,
        batch_ids: Tuple[str, ...],
        held_out: Optional[str],
    ) -> _Task:
        memo_key = task_class, step_id, batch_ids, held_out
        if memo_key not in self.all_tasks:
            task = task_class(step_id, batch_ids, held_out)
            self.all_tasks[memo_key] = task
            self.fresh_tasks.append(task)
        return self.all_tasks[memo_key]


def _create_tasks_fit(
    pipeline: TrainablePipeline[TrainableIndividualOp],
    all_batch_ids: Tuple[str, ...],
    need_metrics: bool,
    incremental: bool,
) -> Dict[_MemoKey, _Task]:
    tg = _TaskGraph(pipeline)
    for step_id in range(len(pipeline.steps_list())):
        task = tg.find_or_create(_TrainTask, step_id, all_batch_ids, None)
        task.deletable_output = False
    if need_metrics:
        for batch_id in all_batch_ids:
            task = tg.find_or_create(_MetricTask, _DUMMY_SCORE_STEP, (batch_id,), None)
            task.deletable_output = False
    while len(tg.fresh_tasks) > 0:
        task = tg.fresh_tasks.pop()
        if isinstance(task, _TrainTask):
            step = pipeline.steps_list()[task.step_id]
            if is_pretrained(step):
                pass
            elif len(task.batch_ids) == 1:
                for pred_step_id in tg.step_id_preds[task.step_id]:
                    task.preds.append(
                        tg.find_or_create(
                            _ApplyTask, pred_step_id, task.batch_ids, None
                        )
                    )
            else:
                if is_associative(step):
                    for batch_id in task.batch_ids:
                        task.preds.append(
                            tg.find_or_create(
                                _TrainTask, task.step_id, (batch_id,), None
                            )
                        )
                elif is_incremental(step):
                    task.preds.append(
                        tg.find_or_create(
                            _TrainTask, task.step_id, task.batch_ids[:-1], None
                        )
                    )
                    for pred_step_id in tg.step_id_preds[task.step_id]:
                        task.preds.append(
                            tg.find_or_create(
                                _ApplyTask, pred_step_id, task.batch_ids[-1:], None
                            )
                        )
                else:
                    for pred_step_id in tg.step_id_preds[task.step_id]:
                        for batch_id in task.batch_ids:
                            task.preds.append(
                                tg.find_or_create(
                                    _ApplyTask, pred_step_id, (batch_id,), None
                                )
                            )
        if isinstance(task, _ApplyTask) and task.step_id != _DUMMY_INPUT_STEP:
            if incremental:
                fit_upto = _get_idx(task.batch_ids[-1]) + 1
            else:
                fit_upto = len(all_batch_ids)
            fold = _get_fold(task.batch_ids[-1])
            task.preds.append(
                tg.find_or_create(
                    _TrainTask,
                    task.step_id,
                    tuple(_batch_id(fold, idx) for idx in range(fit_upto)),
                    None,
                )
            )
            for pred_step_id in tg.step_id_preds[task.step_id]:
                task.preds.append(
                    tg.find_or_create(_ApplyTask, pred_step_id, task.batch_ids, None)
                )
        if isinstance(task, _MetricTask):
            assert len(task.batch_ids) == 1
            task.preds.append(
                tg.find_or_create(_ApplyTask, _DUMMY_INPUT_STEP, task.batch_ids, None)
            )
            sink = pipeline.get_last()
            assert sink is not None
            task.preds.append(
                tg.find_or_create(
                    _ApplyTask,
                    tg.step_ids[sink],
                    task.batch_ids,
                    None,
                )
            )
        for pred_task in task.preds:
            pred_task.succs.append(task)
    return tg.all_tasks


def _batch_ids_except(
    folds: List[str],
    n_batches_per_fold: int,
    held_out: Optional[str],
) -> Tuple[str, ...]:
    return tuple(
        _batch_id(fold, idx)
        for fold in folds
        if fold != held_out
        for idx in range(n_batches_per_fold)
    )


def _create_tasks_cross_val(
    pipeline: TrainablePipeline[TrainableIndividualOp],
    folds: List[str],
    n_batches_per_fold: int,
    same_fold: bool,
    keep_estimator: bool,
) -> Dict[_MemoKey, _Task]:
    tg = _TaskGraph(pipeline)
    held_out: Optional[str]
    for held_out in folds:
        task = tg.find_or_create(
            _MetricTask,
            _DUMMY_SCORE_STEP,
            tuple(_batch_id(held_out, idx) for idx in range(n_batches_per_fold)),
            held_out,
        )
        task.deletable_output = False
    if keep_estimator:
        for step_id in range(len(pipeline.steps_list())):
            for held_out in folds:
                task = tg.find_or_create(
                    _TrainTask,
                    step_id,
                    _batch_ids_except(folds, n_batches_per_fold, held_out),
                    held_out,
                )
                task.deletable_output = False
    while len(tg.fresh_tasks) > 0:
        task = tg.fresh_tasks.pop()
        if isinstance(task, _TrainTask):
            step = pipeline.steps_list()[task.step_id]
            if is_pretrained(step):
                pass
            elif len(task.batch_ids) == 1:
                for pred_step_id in tg.step_id_preds[task.step_id]:
                    if pred_step_id == _DUMMY_INPUT_STEP:
                        held_out = None
                    elif same_fold:
                        held_out = task.held_out
                    else:
                        held_out = _get_fold(task.batch_ids[0])
                    task.preds.append(
                        tg.find_or_create(
                            _ApplyTask, pred_step_id, task.batch_ids, held_out
                        )
                    )
            else:
                if tg.step_id_preds[task.step_id] == [_DUMMY_INPUT_STEP]:
                    held_out = None
                else:
                    if task.held_out is None:
                        hofs = set(folds) - set(_get_fold(b) for b in task.batch_ids)
                        assert len(hofs) == 1, hofs
                        held_out = next(iter(hofs))
                    else:
                        held_out = task.held_out
                if is_associative(step):
                    if not same_fold:
                        held_out = None
                    for batch_id in task.batch_ids:
                        task.preds.append(
                            tg.find_or_create(
                                _TrainTask, task.step_id, (batch_id,), held_out
                            )
                        )
                elif is_incremental(step):
                    task.preds.append(
                        tg.find_or_create(
                            _TrainTask, task.step_id, task.batch_ids[:-1], held_out
                        )
                    )
                    for pred_step_id in tg.step_id_preds[task.step_id]:
                        if pred_step_id != _DUMMY_INPUT_STEP and not same_fold:
                            held_out = _get_fold(task.batch_ids[0])
                        task.preds.append(
                            tg.find_or_create(
                                _ApplyTask, pred_step_id, task.batch_ids[-1:], held_out
                            )
                        )
                else:
                    for pred_step_id in tg.step_id_preds[task.step_id]:
                        if pred_step_id != _DUMMY_INPUT_STEP and not same_fold:
                            held_out = _get_fold(task.batch_ids[0])
                        for batch_id in task.batch_ids:
                            task.preds.append(
                                tg.find_or_create(
                                    _ApplyTask, pred_step_id, (batch_id,), held_out
                                )
                            )
        if isinstance(task, _ApplyTask) and task.step_id != _DUMMY_INPUT_STEP:
            task.preds.append(
                tg.find_or_create(
                    _TrainTask,
                    task.step_id,
                    _batch_ids_except(folds, n_batches_per_fold, task.held_out),
                    None,
                )
            )
            for pred_step_id in tg.step_id_preds[task.step_id]:
                task.preds.append(
                    tg.find_or_create(
                        _ApplyTask,
                        pred_step_id,
                        task.batch_ids,
                        None if pred_step_id == _DUMMY_INPUT_STEP else task.held_out,
                    )
                )
        if isinstance(task, _MetricTask):
            if len(task.batch_ids) == 1:
                task.preds.append(
                    tg.find_or_create(
                        _ApplyTask, _DUMMY_INPUT_STEP, task.batch_ids, None
                    )
                )
                sink = pipeline.get_last()
                assert sink is not None
                task.preds.append(
                    tg.find_or_create(
                        _ApplyTask, tg.step_ids[sink], task.batch_ids, task.held_out
                    )
                )
            else:
                for batch_id in task.batch_ids:
                    task.preds.append(
                        tg.find_or_create(
                            _MetricTask, task.step_id, (batch_id,), task.held_out
                        )
                    )
        for pred_task in task.preds:
            pred_task.succs.append(task)
    return tg.all_tasks


def _analyze_run_trace(stats: _RunStats, trace: List[_TraceRecord]) -> _RunStats:
    memo_key2critical_count: Dict[_MemoKey, int] = {}
    memo_key2critical_time: Dict[_MemoKey, int] = {}
    for record in trace:
        if isinstance(record.task, _TrainTask):
            stats.train_count += 1
            stats.train_time += record.time
        elif isinstance(record.task, _ApplyTask):
            stats.apply_count += 1
            stats.apply_time += record.time
        elif isinstance(record.task, _MetricTask):
            stats.metric_count += 1
            stats.metric_time += record.time
        else:
            assert False, type(record.task)
        critical_count = 1 + max(
            (memo_key2critical_count[p.memo_key()] for p in record.task.preds),
            default=0,
        )
        stats.critical_count = max(critical_count, stats.critical_count)
        memo_key2critical_count[record.task.memo_key()] = critical_count
        critical_time = record.time + max(
            (memo_key2critical_time[p.memo_key()] for p in record.task.preds), default=0
        )
        stats.critical_time = max(critical_time, stats.critical_time)
        memo_key2critical_time[record.task.memo_key()] = critical_time
    return stats


class _BatchCache:
    spill_dir: Optional[tempfile.TemporaryDirectory]
    spill_path: Optional[pathlib.Path]

    def __init__(
        self,
        tasks: Dict[_MemoKey, _Task],
        max_resident: Optional[int],
        prio: Prio,
        verbose: int,
    ):
        self.tasks = tasks
        self.max_resident = sys.maxsize if max_resident is None else max_resident
        self.prio = prio
        self.spill_dir = None
        self.spill_path = None
        self.verbose = verbose
        self.stats = _RunStats()
        self.stats.max_resident = self.max_resident

    def __enter__(self) -> "_BatchCache":
        if self.max_resident < sys.maxsize:
            self.spill_dir = tempfile.TemporaryDirectory()
            self.spill_path = pathlib.Path(self.spill_dir.name)
        return self

    def __exit__(self, value, type, traceback) -> None:
        if self.spill_dir is not None:
            self.spill_dir.cleanup()

    def _get_apply_preds(self, task: _Task) -> List[_ApplyTask]:
        result = [t for t in task.preds if isinstance(t, _ApplyTask)]
        assert all(t.batch is not None for t in result)
        return result

    def estimate_space(self, task: _ApplyTask) -> int:
        other_tasks_with_similar_output = (
            t
            for t in self.tasks.values()
            if t is not task and isinstance(t, _ApplyTask)
            if t.step_id == task.step_id and t.batch is not None
        )
        try:
            surrogate = next(other_tasks_with_similar_output)
            assert isinstance(surrogate, _ApplyTask) and surrogate.batch is not None
            return surrogate.batch.space
        except StopIteration:  # the iterator was empty
            if task.step_id == _DUMMY_INPUT_STEP:
                return 1  # safe to underestimate on first batch scanned
            apply_preds = self._get_apply_preds(task)
            return sum(cast(_Batch, t.batch).space for t in apply_preds)

    def ensure_space(self, amount_needed: int, no_spill_set: Set[_Batch]) -> None:
        no_spill_space = sum(b.space for b in no_spill_set)
        min_resident = amount_needed + no_spill_space
        self.stats.min_resident = max(self.stats.min_resident, min_resident)
        resident_batches = [
            t.batch
            for t in self.tasks.values()
            if isinstance(t, _ApplyTask) and t.batch is not None
            if t.batch.status == _BatchStatus.RESIDENT
        ]
        resident_batches.sort(key=lambda b: self.prio.batch_priority(b))
        resident_batches_space = sum(b.space for b in resident_batches)
        while resident_batches_space + amount_needed > self.max_resident:
            if len(resident_batches) == 0:
                logger.warning(
                    f"ensure_space() failed, amount_needed {amount_needed}, no_spill_space {no_spill_space}, min_resident {min_resident}, max_resident {self.max_resident}"
                )
                break
            batch = resident_batches.pop()
            assert batch.status == _BatchStatus.RESIDENT and batch.task is not None
            if batch in no_spill_set:
                logger.warning(f"aborted spill of batch {batch}")
            else:
                assert self.spill_path is not None, self.max_resident
                batch.spill(self.spill_path)
                self.stats.spill_count += 1
                self.stats.spill_space += batch.space
                if self.verbose >= 2:
                    print(f"spill {batch.X} {batch.y}")
                resident_batches_space -= batch.space

    def load_input_batches(self, task: _Task) -> None:
        apply_preds = self._get_apply_preds(task)
        no_spill_set = cast(Set[_Batch], set(t.batch for t in apply_preds))
        for pred in apply_preds:
            assert pred.batch is not None
            if pred.batch.status == _BatchStatus.SPILLED:
                self.ensure_space(pred.batch.space, no_spill_set)
                if self.verbose >= 2:
                    print(f"load {pred.batch.X} {pred.batch.y}")
                pred.batch.load_spilled()
                self.stats.load_count += 1
                self.stats.load_space += pred.batch.space
        for pred in apply_preds:
            assert pred.batch is not None
            assert pred.batch.status == _BatchStatus.RESIDENT


def _run_tasks_inner(
    tasks: Dict[_MemoKey, _Task],
    pipeline: TrainablePipeline[TrainableIndividualOp],
    batches: Iterable[_RawBatch],
    scoring: Optional[MetricMonoidFactory],
    unique_class_labels: List[Union[str, int, float]],
    all_batch_ids: Tuple[str, ...],
    cache: _BatchCache,
    prio: Prio,
    verbose: int,
    progress_callback: Optional[Callable[[float], None]],
    call_depth: int,
) -> None:
    for task in tasks.values():
        assert task.status is _TaskStatus.FRESH
        if len(task.preds) == 0:
            task.status = _TaskStatus.READY
        else:
            task.status = _TaskStatus.WAITING
    ready_keys = {k for k, t in tasks.items() if t.status is _TaskStatus.READY}

    def find_task(
        task_class: Type["_Task"], task_list: List[_Task]
    ) -> Union[_Task, List[_Task]]:
        task_list = [t for t in task_list if isinstance(t, task_class)]
        if len(task_list) == 1:
            return task_list[0]
        else:
            return task_list

    def try_to_delete_output(task: _Task) -> None:
        if task.deletable_output:
            if all(s.status is _TaskStatus.DONE for s in task.succs):
                if isinstance(task, _ApplyTask):
                    if task.batch is not None:
                        task.batch.delete_if_spilled()
                    task.batch = None
                elif isinstance(task, _TrainTask):
                    task.monoid = None
                    task.trained = None
                elif isinstance(task, _MetricTask):
                    task.mmonoid = None
                else:
                    assert False, type(task)

    def mark_done(task: _Task) -> None:
        try_to_delete_output(task)
        if task.status is _TaskStatus.DONE:
            return
        if task.status is _TaskStatus.READY:
            ready_keys.remove(task.memo_key())
        task.status = _TaskStatus.DONE
        for succ in task.succs:
            if succ.status is _TaskStatus.WAITING:
                if all(p.status is _TaskStatus.DONE for p in succ.preds):
                    succ.status = _TaskStatus.READY
                    ready_keys.add(succ.memo_key())
        for pred in task.preds:
            if all(s.status is _TaskStatus.DONE for s in pred.succs):
                mark_done(pred)
        if isinstance(task, _TrainTask):
            if task.get_operation(pipeline) is _Operation.TO_MONOID:
                if task.monoid is not None and task.monoid.is_absorbing:

                    def is_moot(task2):  # same modulo batch_ids
                        type1, step1, _, hold1 = task.memo_key()
                        type2, step2, _, hold2 = task2.memo_key()
                        return type1 == type2 and step1 == step2 and hold1 == hold2

                    task_monoid = task.monoid  # prevent accidental None assignment
                    for task2 in tasks.values():
                        if task2.status is not _TaskStatus.DONE and is_moot(task2):
                            assert isinstance(task2, _TrainTask)
                            task2.monoid = task_monoid
                            mark_done(task2)

    trace: Optional[List[_TraceRecord]] = [] if verbose >= 2 else None
    batches_iterator = iter(batches)
    while len(ready_keys) > 0:
        if verbose >= 3:
            _visualize_tasks(tasks, pipeline, prio, call_depth + 1, trace)
        task = tasks[min(ready_keys, key=lambda k: prio.task_priority(tasks[k]))]
        operation = task.get_operation(pipeline)
        start_time = time.time() if verbose >= 2 else float("nan")
        if operation is _Operation.SCAN:
            assert isinstance(task, _ApplyTask)
            assert len(task.batch_ids) == 1 and len(task.preds) == 0
            cache.ensure_space(cache.estimate_space(task), set())
            X, y = next(batches_iterator)
            task.batch = _Batch(X, y, task)
        elif operation in [_Operation.TRANSFORM, _Operation.PREDICT]:
            assert isinstance(task, _ApplyTask)
            assert len(task.batch_ids) == 1
            train_pred = cast(_TrainTask, find_task(_TrainTask, task.preds))
            trained = train_pred.get_trained(pipeline)
            apply_preds = [t for t in task.preds if isinstance(t, _ApplyTask)]
            cache.load_input_batches(task)
            if len(apply_preds) == 1:
                assert apply_preds[0].batch is not None
                input_X, input_y = apply_preds[0].batch.Xy
            else:
                assert not any(pred.batch is None for pred in apply_preds)
                input_X = [cast(_Batch, pred.batch).X for pred in apply_preds]
                # The assumption is that input_y is not changed by the preds, so we can
                # use it from any one of them.
                input_y = cast(_Series, cast(_Batch, apply_preds[0].batch).y)
            no_spill_set = cast(Set[_Batch], set(t.batch for t in apply_preds))
            cache.ensure_space(cache.estimate_space(task), no_spill_set)
            if operation is _Operation.TRANSFORM:
                if trained.has_method("transform_X_y"):
                    output_X, output_y = trained.transform_X_y(input_X, input_y)
                else:
                    output_X, output_y = trained.transform(input_X), input_y
                task.batch = _Batch(output_X, output_y, task)
            else:
                y_pred = trained.predict(input_X)
                if isinstance(y_pred, np.ndarray):
                    y_pred = pd.Series(y_pred, input_y.index, input_y.dtype, "y_pred")
                task.batch = _Batch(input_X, y_pred, task)
        elif operation is _Operation.FIT:
            assert isinstance(task, _TrainTask)
            assert all(isinstance(p, _ApplyTask) for p in task.preds)
            apply_preds = [cast(_ApplyTask, p) for p in task.preds]
            assert not any(p.batch is None for p in apply_preds)
            trainable = pipeline.steps_list()[task.step_id]
            if is_pretrained(trainable):
                assert len(task.preds) == 0
                task.trained = cast(TrainedIndividualOp, trainable)
            else:
                cache.load_input_batches(task)
                if len(task.preds) == 1:
                    input_X, input_y = cast(_Batch, apply_preds[0].batch).Xy
                else:
                    assert not is_incremental(trainable)
                    list_X = [cast(_Batch, p.batch).X for p in apply_preds]
                    list_y = [cast(_Batch, p.batch).y for p in apply_preds]
                    if all(isinstance(X, pd.DataFrame) for X in list_X):
                        input_X = pd.concat(list_X)
                        input_y = pd.concat(list_y)
                    elif all(isinstance(X, SparkDataFrame) for X in list_X):
                        input_X = functools.reduce(lambda a, b: a.union(b), list_X)  # type: ignore
                        input_y = functools.reduce(lambda a, b: a.union(b), list_y)  # type: ignore
                    elif all(isinstance(X, np.ndarray) for X in list_X):
                        input_X = np.concatenate(list_X)
                        input_y = np.concatenate(list_y)
                    else:
                        raise ValueError(
                            f"""Input of {type(list_X[0])} is not supported for
                            fit on a non-incremental operator.
                            Supported types are: pandas DataFrame, numpy ndarray, and spark DataFrame."""
                        )

                task.trained = trainable.fit(input_X, input_y)
        elif operation is _Operation.PARTIAL_FIT:
            assert isinstance(task, _TrainTask)
            assert len(task.preds) in [1, 2]
            if len(task.preds) == 1:
                trainee = pipeline.steps_list()[task.step_id]
            else:
                train_pred = cast(_TrainTask, find_task(_TrainTask, task.preds))
                trainee = train_pred.get_trained(pipeline)
            apply_pred = cast(_ApplyTask, find_task(_ApplyTask, task.preds))
            assert apply_pred.batch is not None
            cache.load_input_batches(task)
            input_X, input_y = apply_pred.batch.Xy
            if trainee.is_supervised():
                task.trained = trainee.partial_fit(
                    input_X, input_y, classes=unique_class_labels
                )
            else:
                task.trained = trainee.partial_fit(input_X, input_y)
        elif operation is _Operation.TO_MONOID:
            assert len(task.batch_ids) == 1
            assert all(isinstance(p, _ApplyTask) for p in task.preds)
            assert all(cast(_ApplyTask, p).batch is not None for p in task.preds)
            cache.load_input_batches(task)
            if isinstance(task, _TrainTask):
                assert len(task.preds) == 1
                trainable = pipeline.steps_list()[task.step_id]
                input_X, input_y = task.preds[0].batch.Xy  # type: ignore
                task.monoid = trainable.impl.to_monoid((input_X, input_y))
            elif isinstance(task, _MetricTask):
                assert len(task.preds) == 2
                assert task.preds[0].step_id == _DUMMY_INPUT_STEP
                assert scoring is not None
                X, y_true = task.preds[0].batch.Xy  # type: ignore
                y_pred = task.preds[1].batch.y  # type: ignore
                task.mmonoid = scoring.to_monoid((y_true, y_pred, X))
                if progress_callback is not None:
                    progress_callback(scoring.from_monoid(task.mmonoid))
            else:
                assert False, type(task)
        elif operation is _Operation.COMBINE:
            assert len(task.batch_ids) > 1
            assert len(task.preds) == len(task.batch_ids)
            cache.load_input_batches(task)
            if isinstance(task, _TrainTask):
                assert all(isinstance(p, _TrainTask) for p in task.preds)
                trainable = pipeline.steps_list()[task.step_id]
                monoids = (cast(_TrainTask, p).monoid for p in task.preds)
                task.monoid = functools.reduce(lambda a, b: a.combine(b), monoids)  # type: ignore
            elif isinstance(task, _MetricTask):
                scores = (cast(_MetricTask, p).mmonoid for p in task.preds)
                task.mmonoid = functools.reduce(lambda a, b: a.combine(b), scores)  # type: ignore
            else:
                assert False, type(task)
        else:
            assert False, operation
        if verbose >= 2:
            finish_time = time.time()
            assert trace is not None
            trace.append(_TraceRecord(task, finish_time - start_time))
        mark_done(task)
    if verbose >= 2:
        _visualize_tasks(tasks, pipeline, prio, call_depth + 1, trace)
        assert trace is not None
        print(_analyze_run_trace(cache.stats, trace))


def _run_tasks(
    tasks: Dict[_MemoKey, _Task],
    pipeline: TrainablePipeline[TrainableIndividualOp],
    batches: Iterable[_RawBatch],
    scoring: Optional[MetricMonoidFactory],
    unique_class_labels: List[Union[str, int, float]],
    all_batch_ids: Tuple[str, ...],
    max_resident: Optional[int],
    prio: Prio,
    verbose: int,
    progress_callback: Optional[Callable[[float], None]],
    call_depth: int,
) -> None:
    with _BatchCache(tasks, max_resident, prio, verbose) as cache:
        _run_tasks_inner(
            tasks,
            pipeline,
            batches,
            scoring,
            unique_class_labels,
            all_batch_ids,
            cache,
            prio,
            verbose,
            progress_callback,
            call_depth + 1,
        )


def mockup_data_loader(
    X: pd.DataFrame, y: pd.Series, n_splits: int, astype: str
) -> Iterable[_RawBatch]:
    if n_splits == 1:
        return [(X, y)]
    cv = sklearn.model_selection.KFold(n_splits)
    estimator = sklearn.tree.DecisionTreeClassifier()
    pandas_gen = (
        lale.helpers.split_with_schemas(estimator, X, y, test, train)
        for train, test in cv.split(X, y)
    )  # generator expression returns object with __iter__() method
    if astype == "pandas":
        return pandas_gen
    elif astype == "spark":
        return ((pandas2spark(X), pandas2spark(y)) for X, y in pandas_gen)
    raise ValueError(f"expected astype in ['pandas', 'spark'], got {astype}")


def _clear_tasks_dict(tasks: Dict[_MemoKey, _Task]):
    for task in tasks.values():
        # preds form a garbage collection cycle with succs
        task.preds.clear()
        task.succs.clear()
        # tasks form a garbage collection cycle with batches
        if isinstance(task, _ApplyTask) and task.batch is not None:
            task.batch.task = None
            task.batch = None
    tasks.clear()


def _extract_trained_pipeline(
    pipeline: TrainablePipeline[TrainableIndividualOp],
    folds: List[str],
    n_batches_per_fold: int,
    tasks: Dict[_MemoKey, _Task],
    held_out: Optional[str],
) -> TrainedPipeline:
    batch_ids = _batch_ids_except(folds, n_batches_per_fold, held_out)

    def extract_trained_step(step_id: int) -> TrainedIndividualOp:
        task = cast(_TrainTask, tasks[(_TrainTask, step_id, batch_ids, held_out)])
        return task.get_trained(pipeline)

    step_map = {
        old_step: extract_trained_step(step_id)
        for step_id, old_step in enumerate(pipeline.steps_list())
    }
    trained_edges = [(step_map[x], step_map[y]) for x, y in pipeline.edges()]
    result = TrainedPipeline(
        list(step_map.values()), trained_edges, ordered=True, _lale_trained=True
    )
    return result


def fit_with_batches(
    pipeline: TrainablePipeline[TrainableIndividualOp],
    batches: Iterable[_RawBatch],
    n_batches: int,
    scoring: Optional[MetricMonoidFactory],
    unique_class_labels: List[Union[str, int, float]],
    max_resident: Optional[int],
    prio: Prio,
    incremental: bool,
    verbose: int,
    progress_callback: Optional[Callable[[float], None]],
) -> TrainedPipeline[TrainedIndividualOp]:
    all_batch_ids = tuple(_batch_id("d", idx) for idx in range(n_batches))
    need_metrics = scoring is not None
    tasks = _create_tasks_fit(pipeline, all_batch_ids, need_metrics, incremental)
    if verbose >= 3:
        _visualize_tasks(tasks, pipeline, prio, call_depth=2, trace=None)
    _run_tasks(
        tasks,
        pipeline,
        batches,
        scoring,
        unique_class_labels,
        all_batch_ids,
        max_resident,
        prio,
        verbose,
        progress_callback,
        call_depth=2,
    )
    trained_pipeline = _extract_trained_pipeline(
        pipeline, ["d"], n_batches, tasks, None
    )
    _clear_tasks_dict(tasks)
    return trained_pipeline


def _extract_scores(
    pipeline: TrainablePipeline[TrainableIndividualOp],
    folds: List[str],
    n_batches_per_fold: int,
    scoring: MetricMonoidFactory,
    tasks: Dict[_MemoKey, _Task],
) -> List[float]:
    def extract_score(held_out: str) -> float:
        batch_ids = tuple(_batch_id(held_out, idx) for idx in range(n_batches_per_fold))
        task = tasks[(_MetricTask, _DUMMY_SCORE_STEP, batch_ids, held_out)]
        assert isinstance(task, _MetricTask) and task.mmonoid is not None
        return scoring.from_monoid(task.mmonoid)

    scores = [extract_score(held_out) for held_out in folds]
    return scores


def cross_val_score(
    pipeline: TrainablePipeline[TrainableIndividualOp],
    batches: Iterable[_RawBatch],
    n_batches: int,
    n_folds: int,
    n_batches_per_fold: int,
    scoring: MetricMonoidFactory,
    unique_class_labels: List[Union[str, int, float]],
    max_resident: Optional[int],
    prio: Prio,
    same_fold: bool,
    verbose: int,
) -> List[float]:
    assert n_batches == n_folds * n_batches_per_fold
    folds = [chr(ord("d") + i) for i in range(n_folds)]
    all_batch_ids = tuple(
        _batch_id(fold, idx) for fold in folds for idx in range(n_batches_per_fold)
    )
    tasks = _create_tasks_cross_val(
        pipeline, folds, n_batches_per_fold, same_fold, False
    )
    if verbose >= 3:
        _visualize_tasks(tasks, pipeline, prio, call_depth=2, trace=None)
    _run_tasks(
        tasks,
        pipeline,
        batches,
        scoring,
        unique_class_labels,
        all_batch_ids,
        max_resident,
        prio,
        verbose,
        None,
        call_depth=2,
    )
    scores = _extract_scores(pipeline, folds, n_batches_per_fold, scoring, tasks)
    _clear_tasks_dict(tasks)
    return scores


def cross_validate(
    pipeline: TrainablePipeline[TrainableIndividualOp],
    batches: Iterable[_RawBatch],
    n_batches: int,
    n_folds: int,
    n_batches_per_fold: int,
    scoring: MetricMonoidFactory,
    unique_class_labels: List[Union[str, int, float]],
    max_resident: Optional[int],
    prio: Prio,
    same_fold: bool,
    return_estimator: bool,
    verbose: int,
) -> Dict[str, Union[List[float], List[TrainedPipeline]]]:
    assert n_batches == n_folds * n_batches_per_fold
    folds = [chr(ord("d") + i) for i in range(n_folds)]
    all_batch_ids = tuple(
        _batch_id(fold, idx) for fold in folds for idx in range(n_batches_per_fold)
    )
    tasks = _create_tasks_cross_val(
        pipeline, folds, n_batches_per_fold, same_fold, return_estimator
    )
    if verbose >= 3:
        _visualize_tasks(tasks, pipeline, prio, call_depth=2, trace=None)
    _run_tasks(
        tasks,
        pipeline,
        batches,
        scoring,
        unique_class_labels,
        all_batch_ids,
        max_resident,
        prio,
        verbose,
        None,
        call_depth=2,
    )
    result: Dict[str, Union[List[float], List[TrainedPipeline]]] = {}
    result["test_score"] = _extract_scores(
        pipeline, folds, n_batches_per_fold, scoring, tasks
    )
    if return_estimator:
        result["estimator"] = [
            _extract_trained_pipeline(
                pipeline, folds, n_batches_per_fold, tasks, held_out
            )
            for held_out in folds
        ]
    _clear_tasks_dict(tasks)
    return result
