import json
import shutil
import os
from odoo import models, fields, api
from odoo.exceptions import UserError


class CroseNrPackage(models.Model):
    _name = "crose.nr.package"
    _description = "Node-RED安装包"

    name = fields.Char(string="包名称", required=True)
    version = fields.Char(string="版本", required=True)
    environment = fields.Selection([
        ('staging', 'Staging'),
        ('prod', 'Production')
    ], string="环境", default='staging', required=True)
    component_id = fields.Many2one('crose.component', string='所属组件', required=True, ondelete='cascade')

    @api.constrains('name', 'version', 'component_id')
    def _check_name_version_unique(self):
        for record in self:
            existing = self.search_count([
                ('name', '=', record.name),
                ('version', '=', record.version),
                ('component_id', '=', record.component_id.id),
                ('id', '!=', record.id),
            ])
            if existing:
                raise UserError('同一组件下包名和版本组合必须唯一！')

    def _copy_package_to_prod(self, staging_storage, prod_storage, package_name, version, copied_packages=None):
        if copied_packages is None:
            copied_packages = set()

        pkg_key = f"{package_name}@{version}"
        if pkg_key in copied_packages:
            return copied_packages

        staging_pkg_dir = os.path.join(staging_storage, package_name)
        prod_pkg_dir = os.path.join(prod_storage, package_name)

        if not os.path.exists(staging_pkg_dir):
            raise UserError(f"Staging 环境中找不到包目录: {staging_pkg_dir}")

        os.makedirs(prod_pkg_dir, exist_ok=True)

        base_name = package_name.split('/')[-1] if '/' in package_name else package_name
        staging_tgz = os.path.join(staging_pkg_dir, f"{base_name}-{version}.tgz")
        prod_tgz = os.path.join(prod_pkg_dir, f"{base_name}-{version}.tgz")
        staging_package_json = os.path.join(staging_pkg_dir, 'package.json')
        prod_package_json = os.path.join(prod_pkg_dir, 'package.json')

        if not os.path.exists(staging_tgz):
            raise UserError(f"Staging 环境中找不到包文件: {staging_tgz}")

        shutil.copy2(staging_tgz, prod_tgz)

        prod_pkg_data = {}
        if os.path.exists(prod_package_json):
            with open(prod_package_json, 'r', encoding='utf-8') as f:
                prod_pkg_data = json.load(f)

        staging_pkg_data = {}
        if os.path.exists(staging_package_json):
            with open(staging_package_json, 'r', encoding='utf-8') as f:
                staging_pkg_data = json.load(f)

        if 'versions' not in prod_pkg_data:
            prod_pkg_data['name'] = package_name
            prod_pkg_data['versions'] = {}

        if version in staging_pkg_data.get('versions', {}):
            prod_pkg_data['versions'][version] = staging_pkg_data['versions'][version]

        if staging_pkg_data.get('time') and 'time' not in prod_pkg_data:
            prod_pkg_data['time'] = staging_pkg_data['time']

        with open(prod_package_json, 'w', encoding='utf-8') as f:
            json.dump(prod_pkg_data, f, indent=2, ensure_ascii=False)

        copied_packages.add(pkg_key)

        version_data = staging_pkg_data.get('versions', {}).get(version, {})
        dependencies = version_data.get('dependencies', {})

        for dep_name in dependencies.keys():
            dep_base_name = dep_name.split('/')[-1] if '/' in dep_name else dep_name
            dep_dir_name = dep_name.replace('/', os.sep)

            dep_storage_path = os.path.join(staging_storage, dep_dir_name)
            if os.path.exists(dep_storage_path):
                for item in os.listdir(dep_storage_path):
                    item_dir = os.path.join(dep_storage_path, item)
                    if os.path.isdir(item_dir) and item.startswith(dep_base_name + '-'):
                        dep_version = item[len(dep_base_name) + 1:]
                        self._copy_package_to_prod(staging_storage, prod_storage, dep_dir_name, dep_version, copied_packages)

        return copied_packages

    def _copy_all_packages(self, staging_storage, prod_storage, copied_packages=None):
        if copied_packages is None:
            copied_packages = set()

        if not os.path.exists(staging_storage):
            return copied_packages

        for item in os.listdir(staging_storage):
            item_path = os.path.join(staging_storage, item)
            if not os.path.isdir(item_path):
                continue

            if item.startswith('@'):
                for sub_item in os.listdir(item_path):
                    sub_item_path = os.path.join(item_path, sub_item)
                    if os.path.isdir(sub_item_path):
                        tgz_files = [f for f in os.listdir(sub_item_path) if f.endswith('.tgz')]
                        for tgz in tgz_files:
                            version = tgz.rsplit('-', 1)[-1].replace('.tgz', '')
                            full_name = os.path.join(item, sub_item)
                            pkg_key = f"{full_name}@{version}"
                            if pkg_key not in copied_packages:
                                self._copy_package_to_prod(staging_storage, prod_storage, full_name, version, copied_packages)
            else:
                tgz_files = [f for f in os.listdir(item_path) if f.endswith('.tgz')]
                for tgz in tgz_files:
                    version = tgz.rsplit('-', 1)[-1].replace('.tgz', '')
                    pkg_key = f"{item}@{version}"
                    if pkg_key not in copied_packages:
                        self._copy_package_to_prod(staging_storage, prod_storage, item, version, copied_packages)

        return copied_packages

    def action_publish(self):
        for pkg in self:
            if pkg.environment != 'staging':
                raise UserError(f"包 {pkg.name} v{pkg.version} 不在 staging 环境，无法发布！")

            staging_storage = self.env['crose.component']._get_staging_storage_path(pkg.component_id)
            prod_storage = self.env['crose.component']._get_prod_storage_path(pkg.component_id)

            self._copy_package_to_prod(staging_storage, prod_storage, pkg.name, pkg.version)
            self._copy_all_packages(staging_storage, prod_storage)

            pkg.write({'environment': 'prod'})

    @api.model
    def _update_verdaccio_db(self, component_id, name, version, environment):
        pass