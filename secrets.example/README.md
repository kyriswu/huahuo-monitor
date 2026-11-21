真实文件放在仓库根目录 `secrets/`，该目录被 Git 忽略。

- `grafana_admin_password`：Grafana 初次初始化的管理员密码，仅一行；迁移时保留原文件与 `grafana_data` 卷。
- `rds-api.json`：参照本目录同名空模板，填入具有 RDS `DescribeSlowLogRecords` 权限的凭据；使用临时凭据时可增加 `security_token`。
- `login.txt`：原机器的人工登录备忘，如有则随私有备份迁移。

Dozzle 的 `dozzle/users.yml` 也不入 Git；迁移时从私有备份恢复。全新安装可用官方镜像交互生成：

```sh
mkdir -p secrets dozzle
chmod 700 secrets
docker run --rm -it amir20/dozzle:v11.4.3 generate admin
```

将该命令输出的完整 `users:` YAML 保存至 `dozzle/users.yml`，不要把它提交到 Git。不要把真实密码、AccessKey 或私钥填进本目录的模板。
