**********
Translator
**********

The translator turns a SQL string into a Redis ``FT.SEARCH`` or ``FT.AGGREGATE``
command. It does not execute anything; use :class:`~sql_redis.Executor` for that.

Translator
==========

.. currentmodule:: sql_redis

.. autoclass:: Translator
   :members:
   :inherited-members:

TranslatedQuery
===============

.. currentmodule:: sql_redis

.. autoclass:: TranslatedQuery
   :members:
