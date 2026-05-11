import json
import shutil
import os
import tempfile
import base64
import hashlib
import tarfile
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import requests
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class CroseNrPackage(models.Model):
    _name = "crose.nr.package"
    _description = "Node-RED Package"

    name = fields.Char(string="Package Name", required=True)
    version = fields.Char(string="Version", required=True)
    environment = fields.Selection([
        ('staging', 'Staging'),
        ('prod', 'Production')
    ], string="Environment", default='staging', required=True)
    component_id = fields.Many2one('crose.component', string='Component', required=True, ondelete='cascade')

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
                raise UserError(_('The combination of package name and version must be unique within the same component.'))

    def _copy_package_to_prod(self, staging_storage, prod_storage, package_name, version, copied_packages=None):
        if copied_packages is None:
            copied_packages = set()

        pkg_key = f"{package_name}@{version}"
        if pkg_key in copied_packages:
            return copied_packages

        staging_pkg_dir = os.path.join(staging_storage, package_name)
        prod_pkg_dir = os.path.join(prod_storage, package_name)

        if not os.path.exists(staging_pkg_dir):
            raise UserError(_("Package directory not found in the Staging environment: %(path)s", path=staging_pkg_dir))

        os.makedirs(prod_pkg_dir, exist_ok=True)

        base_name = package_name.split('/')[-1] if '/' in package_name else package_name
        staging_tgz = os.path.join(staging_pkg_dir, f"{base_name}-{version}.tgz")
        prod_tgz = os.path.join(prod_pkg_dir, f"{base_name}-{version}.tgz")
        staging_package_json = os.path.join(staging_pkg_dir, 'package.json')
        prod_package_json = os.path.join(prod_pkg_dir, 'package.json')

        if not os.path.exists(staging_tgz):
            raise UserError(_("Package file not found in the Staging environment: %(path)s", path=staging_tgz))

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

    def _get_nexus_auth(self, component):
        component.ensure_one()
        account = component.account_ids.filtered(lambda rec: rec.is_primary)[:1] or component.account_ids[:1]
        if not account:
            raise UserError(_("No account configured for component %(name)s.", name=component.name))
        username = (account.username or "").strip()
        password = account._get_plain_password()
        if not username or not password:
            raise UserError(
                _("Component %(name)s account credentials are incomplete. Please configure username and password.",
                  name=component.name)
            )
        return username, password

    def _extract_repository_from_search_url(self, search_url):
        parsed = urlparse(search_url or "")
        query_dict = parse_qs(parsed.query or "", keep_blank_values=False)
        repository = ""
        if isinstance(query_dict, dict):
            repository = ((query_dict.get("repository") or [""])[0] or "").strip()
        if not repository:
            raise UserError(
                _("Nexus search URL must include repository query parameter. URL: %(url)s", url=search_url)
            )
        return parsed, repository

    def _nexus_search_package_asset(self, stage_search_url, package_name, version, auth):
        parsed, repository_name = self._extract_repository_from_search_url(stage_search_url)
        query_dict = parse_qs(parsed.query or "", keep_blank_values=False)
        query_dict["name"] = [package_name]
        query_dict["version"] = [version]
        search_url = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "",
                urlencode(query_dict, doseq=True),
                "",
            )
        )
        response = requests.get(
            search_url,
            auth=auth,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        items = payload.get("items") if isinstance(payload, dict) else []
        if not items:
            raise UserError(
                _("Cannot find package %(name)s v%(version)s in Nexus repository %(repo)s.",
                  name=package_name, version=version, repo=repository_name)
            )
        for item in items:
            assets = item.get("assets") if isinstance(item, dict) else []
            for asset in assets or []:
                if not isinstance(asset, dict):
                    continue
                download_url = str(asset.get("downloadUrl") or "").strip()
                asset_path = str(asset.get("path") or "").strip()
                if download_url and asset_path:
                    return download_url, asset_path
        raise UserError(
            _("No downloadable asset found for package %(name)s v%(version)s in repository %(repo)s.",
              name=package_name, version=version, repo=repository_name)
        )

    def _download_package_to_temp(self, download_url, auth):
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".tgz")
        temp_path = temp_file.name
        try:
            with temp_file:
                with requests.get(download_url, auth=auth, stream=True, timeout=60) as response:
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            temp_file.write(chunk)
            return temp_path
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def _build_npm_publish_payload(self, package_name, version, asset_path, local_file_path, tarball_url):
        tarball_name = os.path.basename(asset_path) or f"{package_name.split('/')[-1]}-{version}.tgz"
        with open(local_file_path, "rb") as file_obj:
            tgz_bytes = file_obj.read()
        sha1_value = hashlib.sha1(tgz_bytes).hexdigest()
        sha512_value = base64.b64encode(hashlib.sha512(tgz_bytes).digest()).decode("utf-8")
        package_json = {}
        with tarfile.open(local_file_path, "r:gz") as tar_obj:
            package_json_member = None
            for member in tar_obj.getmembers():
                if member.isfile() and member.name.endswith("/package.json"):
                    package_json_member = member
                    break
            if package_json_member:
                extracted = tar_obj.extractfile(package_json_member)
                if extracted:
                    package_json = json.loads(extracted.read().decode("utf-8"))
        if not isinstance(package_json, dict):
            package_json = {}
        package_json["name"] = package_name
        package_json["version"] = version
        dist = package_json.get("dist") if isinstance(package_json.get("dist"), dict) else {}
        dist.update(
            {
                "shasum": sha1_value,
                "integrity": f"sha512-{sha512_value}",
                "tarball": tarball_url,
            }
        )
        package_json["dist"] = dist
        package_json["_id"] = f"{package_name}@{version}"
        return {
            "_id": package_name,
            "name": package_name,
            "dist-tags": {"latest": version},
            "versions": {version: package_json},
            "_attachments": {
                tarball_name: {
                    "content_type": "application/octet-stream",
                    "data": base64.b64encode(tgz_bytes).decode("utf-8"),
                    "length": len(tgz_bytes),
                }
            },
        }

    def _upload_package_to_nexus(self, prod_search_url, package_name, version, asset_path, local_file_path, auth):
        parsed, repository_name = self._extract_repository_from_search_url(prod_search_url)
        registry_base = f"{parsed.scheme}://{parsed.netloc}/repository/{repository_name}"
        tarball_url = f"{registry_base}/{asset_path.lstrip('/')}"
        package_endpoint = package_name.replace("/", "%2f")
        publish_url = f"{registry_base}/{package_endpoint}"
        payload = self._build_npm_publish_payload(
            package_name=package_name,
            version=version,
            asset_path=asset_path,
            local_file_path=local_file_path,
            tarball_url=tarball_url,
        )
        response = requests.put(
            publish_url,
            json=payload,
            auth=auth,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        response.raise_for_status()

    def action_publish(self):
        for pkg in self:
            if pkg.environment != 'staging':
                raise UserError(_("Package %(name)s v%(version)s is not in the Staging environment and cannot be published.", name=pkg.name, version=pkg.version))

            component = pkg.component_id
            if component.component_type != "npm":
                raise UserError(_("Only npm component packages can be published via Nexus."))
            metadata_dict = component._metadata_dict()
            stage_path = str(metadata_dict.get("node-red-stage") or "").strip()
            prod_path = str(metadata_dict.get("node-red-prod") or "").strip()
            if not stage_path or not prod_path:
                raise UserError(
                    _("Component metadata must include node-red-stage and node-red-prod repository URLs.")
                )
            stage_search_url = component._resolve_metadata_endpoint("node-red-stage")
            prod_search_url = component._resolve_metadata_endpoint("node-red-prod")
            auth = self._get_nexus_auth(component)
            download_url, asset_path = self._nexus_search_package_asset(
                stage_search_url=stage_search_url,
                package_name=pkg.name,
                version=pkg.version,
                auth=auth,
            )
            temp_path = self._download_package_to_temp(download_url, auth)
            try:
                self._upload_package_to_nexus(
                    prod_search_url=prod_search_url,
                    package_name=pkg.name,
                    version=pkg.version,
                    asset_path=asset_path,
                    local_file_path=temp_path,
                    auth=auth,
                )
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            pkg.write({'environment': 'prod'})

    @api.model
    def _update_verdaccio_db(self, component_id, name, version, environment):
        pass
