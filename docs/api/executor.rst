********
Executor
********

The executor runs a translated SQL query against Redis and parses the response
into a :class:`~sql_redis.QueryResult`. There are sync and async variants and
factory functions that wire up a schema registry for you.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Symbol
     - Description
   * - :ref:`executor_api`
     - Sync executor.
   * - :ref:`asyncexecutor_api`
     - Async executor.
   * - :ref:`createexecutor_api`
     - Factory for the sync executor with a configurable cache strategy.
   * - :ref:`createasyncexecutor_api`
     - Factory for the async executor.
   * - :ref:`queryresult_api`
     - Result rows and total count.
   * - :ref:`schemacachestrategy_api`
     - ``"lazy"`` or ``"load_all"`` literal.

.. _executor_api:

Executor
========

.. currentmodule:: sql_redis

.. autoclass:: Executor
   :members:
   :inherited-members:

.. _asyncexecutor_api:

AsyncExecutor
=============

.. currentmodule:: sql_redis

.. autoclass:: AsyncExecutor
   :members:
   :inherited-members:

.. _createexecutor_api:

create_executor
===============

.. currentmodule:: sql_redis

.. autofunction:: create_executor

.. _createasyncexecutor_api:

create_async_executor
=====================

.. currentmodule:: sql_redis

.. autofunction:: create_async_executor

.. _queryresult_api:

QueryResult
===========

.. currentmodule:: sql_redis

.. autoclass:: QueryResult
   :members:

.. _schemacachestrategy_api:

SchemaCacheStrategy
===================

.. currentmodule:: sql_redis

.. autodata:: SchemaCacheStrategy

.. _version_api:

__version__
===========

.. currentmodule:: sql_redis

.. autodata:: __version__
   :annotation: = "<current release>"

The installed package version, as a string. Useful for log lines, bug
reports, and version-gated feature checks.
