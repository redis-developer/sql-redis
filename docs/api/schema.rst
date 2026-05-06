*****************
Schema Registries
*****************

The schema registry caches index field types loaded from Redis via ``FT.INFO``.
There are sync and async variants. Conceptual background is in
:doc:`/concepts/schema-aware-translation`.

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Class
     - Description
   * - :ref:`schemaregistry_api`
     - Sync registry. Lazy and eager loading, polling for index changes.
   * - :ref:`asyncschemaregistry_api`
     - Async registry. Coalesced concurrent loads, cancellation-safe.

.. _schemaregistry_api:

SchemaRegistry
==============

.. currentmodule:: sql_redis

.. autoclass:: SchemaRegistry
   :members:
   :inherited-members:

.. _asyncschemaregistry_api:

AsyncSchemaRegistry
===================

.. currentmodule:: sql_redis

.. autoclass:: AsyncSchemaRegistry
   :members:
   :inherited-members:
