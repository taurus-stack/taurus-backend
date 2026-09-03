"""内置Adapterdirectory.

Convention:
- 通用Adapter直接放在此directory下, 如 script.py / command.py;
- Page三方/实验性Adapter放 contrib/ childdirectory, 不在默认 INSTALLED_UNIT_ADAPTERS load.
- Unit test专用Adapter放 _testing_noop.py / _testing_fail.py(以 _ 开头不会被误Found).
"""