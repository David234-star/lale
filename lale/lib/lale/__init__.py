# Copyright 2019 IBM Corporation
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

"""
Lale operators with schemas.

Operators
=========

Estimators:

* lale.lib.lale. `AutoPipeline`_
* lale.lib.lale. `GridSearchCV`_
* lale.lib.lale. `HalvingGridSearchCV`_
* lale.lib.lale. `Hyperopt`_
* lale.lib.lale. `OptimizeLast`_
* lale.lib.lale. `OptimizeSuffix`_
* lale.lib.lale. `SMAC`_
* lale.lib.lale. `TopKVotingClassifier`_

Transformers:

* lale.lib.rasl. `Aggregate`_
* lale.lib.rasl. `Alias`_
* lale.lib.rasl. `Batching`_
* lale.lib.rasl. `ConcatFeatures`_
* lale.lib.rasl. `Filter`_
* lale.lib.rasl. `GroupBy`_
* lale.lib.rasl. `Join`_
* lale.lib.rasl. `Map`_
* lale.lib.lale. `NoOp`_
* lale.lib.rasl. `OrderBy`_
* lale.lib.rasl. `Project`_
* lale.lib.rasl. `Relational`_
* lale.lib.lale. `SampleBasedVoting`_
* lale.lib.rasl. `Scan`_
* lale.lib.rasl. `SplitXy`_
* lale.lib.lale. `Tee`_

Estimators and transformers:

* lale.lib.lale. `Both`_
* lale.lib.lale. `IdentityWrapper`_
* lale.lib.lale. `Observing`_

.. _`AutoPipeline`: lale.lib.lale.auto_pipeline.html
.. _`GridSearchCV`: lale.lib.lale.grid_search_cv.html
.. _`HalvingGridSearchCV`: lale.lib.lale.halving_grid_search_cv.html
.. _`Hyperopt`: lale.lib.lale.hyperopt.html
.. _`OptimizeLast`: lale.lib.lale.optimize_last.html
.. _`OptimizeSuffix`: lale.lib.lale.optimize_suffix.html

.. _`TopKVotingClassifier`: lale.lib.lale.topk_voting_classifier.html
.. _`SMAC`: lale.lib.lale.smac.html
.. _`Batching`: lale.lib.rasl.batching.html
.. _`ConcatFeatures`: lale.lib.rasl.concat_features.html
.. _`NoOp`: lale.lib.lale.no_op.html
.. _`Project`: lale.lib.rasl.project.html
.. _`SampleBasedVoting`: lale.lib.lale.sample_based_voting.html
.. _`Aggregate`: lale.lib.rasl.aggregate.html
.. _`Filter`: lale.lib.rasl.filter.html
.. _`GroupBy`: lale.lib.rasl.group_by.html
.. _`Map`: lale.lib.rasl.map.html
.. _`OrderBy`: lale.lib.rasl.orderby.html
.. _`Join`: lale.lib.rasl.join.html
.. _`Alias`: lale.lib.rasl.alias.html
.. _`Scan`: lale.lib.rasl.scan.html
.. _`SplitXy`: lale.lib.rasl.split_xy.html
.. _`Relational`: lale.lib.rasl.relational.html
.. _`Both`: lale.lib.lale.both.html
.. _`IdentityWrapper`: lale.lib.lale.identity_wrapper.html
.. _`Observing`: lale.lib.lale.observing.html
.. _`Tee`: lale.lib.lale.tee.html

Functions:
==========

* lale.lib.lale. `categorical`_
* lale.lib.lale. `date_time`_
* SparkExplainer. `spark_explainer`_

.. _`categorical`: lale.lib.rasl.functions.html#lale.lib.rasl.functions.categorical
.. _`date_time`: lale.lib.rasl.functions.html#lale.lib.rasl.functions.date_time
.. _`spark_explainer`: lale.lib.rasl.spark_explainer.html
"""

# Note: all imports should be done as
# from .xxx import XXX as XXX
# this ensures that pyright considers them to be publicly available
# and not private imports (this affects lale users that use pyright)

from lale.lib.rasl import Aggregate as Aggregate
from lale.lib.rasl import Alias as Alias
from lale.lib.rasl import Batching as Batching
from lale.lib.rasl import ConcatFeatures as ConcatFeatures
from lale.lib.rasl import Filter as Filter
from lale.lib.rasl import GroupBy as GroupBy
from lale.lib.rasl import Join as Join
from lale.lib.rasl import Map as Map
from lale.lib.rasl import OrderBy as OrderBy
from lale.lib.rasl import Project as Project
from lale.lib.rasl import Relational as Relational
from lale.lib.rasl import Scan as Scan
from lale.lib.rasl import SplitXy as SplitXy
from lale.lib.rasl import categorical as categorical
from lale.lib.rasl import date_time as date_time
from lale.lib.rasl import spark_explainer as spark_explainer

# estimators
from .auto_pipeline import AutoPipeline as AutoPipeline

# estimators and transformers
from .both import Both as Both

# functions
from .grid_search_cv import GridSearchCV as GridSearchCV
from .halving_grid_search_cv import HalvingGridSearchCV as HalvingGridSearchCV
from .hyperopt import Hyperopt as Hyperopt
from .identity_wrapper import IdentityWrapper as IdentityWrapper
from .no_op import NoOp as NoOp
from .observing import Observing as Observing
from .optimize_last import OptimizeLast as OptimizeLast
from .optimize_suffix import OptimizeSuffix as OptimizeSuffix
from .sample_based_voting import SampleBasedVoting as SampleBasedVoting
from .smac import SMAC as SMAC
from .tee import Tee as Tee
from .topk_voting_classifier import TopKVotingClassifier as TopKVotingClassifier
