from odoo import http
from odoo.http import request
import json
from datetime import datetime, timedelta
from odoo import fields
import contextlib

class OverviewController(http.Controller):

    def _range_start_dt(self, time_range):
        now_dt = fields.Datetime.to_datetime(fields.Datetime.now())
        if time_range == "week":
            return now_dt - timedelta(days=7), now_dt
        if time_range == "month":
            return now_dt - timedelta(days=30), now_dt
        return now_dt.replace(hour=0, minute=0, second=0, microsecond=0), now_dt

    def _normalize_time_ms(self, raw):
        if raw is None:
            return None
        with contextlib.suppress(Exception):
            val = int(float(raw))
            if val > 10**12:
                return val
            if val > 10**9:
                return val * 1000
        return None

    def _safe_float(self, raw):
        with contextlib.suppress(Exception):
            return float(raw)
        return None

    def _coerce_metric_record(self, obj, default_time_ms=None):
        if isinstance(obj, str):
            with contextlib.suppress(Exception):
                obj = json.loads(obj)
        if not isinstance(obj, dict):
            return None
        time_ms = self._normalize_time_ms(obj.get("time")) or default_time_ms
        if not time_ms:
            return None
        cpu = self._safe_float(obj.get("cpu"))
        mem = self._safe_float(obj.get("mem", obj.get("memory")))
        disk = self._safe_float(obj.get("disk"))
        network = self._safe_float(obj.get("network"))
        return {
            "time": time_ms,
            "cpu": cpu,
            "memory": mem,
            "disk": disk,
            "network": network,
        }

    def _coerce_activity_record(self, obj, default_time_ms=None):
        if isinstance(obj, str):
            with contextlib.suppress(Exception):
                obj = json.loads(obj)
        if not isinstance(obj, dict):
            return None
        time_ms = self._normalize_time_ms(obj.get("time")) or default_time_ms
        if not time_ms:
            processed_at = obj.get("processedAt")
            if isinstance(processed_at, str) and processed_at.strip():
                iso_text = processed_at.strip().replace("Z", "+00:00")
                with contextlib.suppress(Exception):
                    time_ms = int(datetime.fromisoformat(iso_text).timestamp() * 1000)
        if not time_ms:
            return None
        result = str(obj.get("result", "")).strip().upper()
        return {"time": time_ms, "result": result}

    def _get_redis_component(self, env):
        redis_comp = env["crose.component"].search([("component_type", "=", "redis"), ("status", "=", "online")], limit=1)
        if not redis_comp:
            redis_comp = env["crose.component"].search([("component_type", "=", "redis")], limit=1)
        return redis_comp

    def _build_redis_client(self, redis_comp, db_value):
        metadata = {}
        if redis_comp.metadata:
            with contextlib.suppress(Exception):
                metadata = json.loads(redis_comp.metadata)
        username = metadata.get("username")
        password = metadata.get("password")
        with contextlib.suppress(Exception):
            db_value = int(db_value)
        import redis
        return redis.Redis(
            host=redis_comp.host or "localhost",
            port=redis_comp.port or 6379,
            username=username,
            password=password,
            db=db_value,
            decode_responses=True,
            socket_connect_timeout=5,
        )

    def _load_metrics_series_from_redis(self, env, time_range):
        start_dt, end_dt = self._range_start_dt(time_range)
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        points = []

        redis_comp = self._get_redis_component(env)
        if not redis_comp:
            return []

        metrics_db = 0
        if redis_comp.metadata:
            with contextlib.suppress(Exception):
                metadata = json.loads(redis_comp.metadata)
                metrics_db = metadata.get("db", 0)
        client = self._build_redis_client(redis_comp, metrics_db)

        key_history = env["ir.config_parameter"].sudo().get_param("feitas_iot.overview.metrics_history_key", "host:metrics:history")
        key_current = env["ir.config_parameter"].sudo().get_param("feitas_iot.overview.metrics_current_key", "host:metrics:current")

        history_type = client.type(key_history)
        if isinstance(history_type, bytes):
            history_type = history_type.decode(errors="ignore")

        if history_type == "zset":
            for raw, score in client.zrangebyscore(key_history, start_ms, end_ms, withscores=True):
                record = self._coerce_metric_record(raw, default_time_ms=self._normalize_time_ms(score))
                if record:
                    points.append(record)
        elif history_type == "list":
            values = client.lrange(key_history, -5000, -1)
            for raw in values:
                record = self._coerce_metric_record(raw)
                if record and start_ms <= record["time"] <= end_ms:
                    points.append(record)
        elif history_type == "stream":
            entries = client.xrange(key_history, min=str(start_ms), max=str(end_ms), count=5000)
            for stream_id, fields_map in entries:
                record = self._coerce_metric_record(fields_map)
                if not record:
                    record = self._coerce_metric_record(fields_map.get("data"))
                if record:
                    points.append(record)

        if not points:
            current_type = client.type(key_current)
            if isinstance(current_type, bytes):
                current_type = current_type.decode(errors="ignore")
            current_value = None
            if current_type == "string":
                current_value = client.get(key_current)
            elif current_type == "hash":
                current_value = client.hgetall(key_current)
            current_record = self._coerce_metric_record(current_value, default_time_ms=end_ms)
            if current_record:
                points.append(current_record)

        points.sort(key=lambda x: x["time"])
        return [p for p in points if start_ms <= p["time"] <= end_ms]

    def _load_upload_activity_stats(self, env, time_range):
        start_dt, end_dt = self._range_start_dt(time_range)
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        stats = {"total_activities": 0, "success": 0, "failed": 0}
        redis_comp = self._get_redis_component(env)
        if not redis_comp:
            return stats

        client = self._build_redis_client(redis_comp, 0)

        def _consume_record(raw_value, default_time_ms=None):
            rec = self._coerce_activity_record(raw_value, default_time_ms=default_time_ms)
            if not rec:
                return
            if not (start_ms <= rec["time"] <= end_ms):
                return
            stats["total_activities"] += 1
            if rec["result"] == "SUCCESS":
                stats["success"] += 1
            elif rec["result"] == "FAIL":
                stats["failed"] += 1

        for key in client.scan_iter(match="upload:*"):
            key_type = client.type(key)
            if isinstance(key_type, bytes):
                key_type = key_type.decode(errors="ignore")
            if key_type == "string":
                _consume_record(client.get(key))
            elif key_type == "list":
                for raw in client.lrange(key, 0, -1):
                    _consume_record(raw)
            elif key_type == "zset":
                for raw, score in client.zrange(key, 0, -1, withscores=True):
                    _consume_record(raw, default_time_ms=self._normalize_time_ms(score))
            elif key_type == "stream":
                for stream_id, fields_map in client.xrange(key, count=5000):
                    default_time = None
                    if isinstance(stream_id, str) and "-" in stream_id:
                        with contextlib.suppress(Exception):
                            default_time = int(stream_id.split("-", 1)[0])
                    _consume_record(fields_map)
                    if "data" in fields_map:
                        _consume_record(fields_map.get("data"), default_time_ms=default_time)
            elif key_type == "hash":
                _consume_record(client.hgetall(key))
        return stats

    @http.route('/feitas_iot/get_component_status', type='jsonrpc', auth='user')
    def get_component_status(self, time_range="today"):
        env = request.env
        env['crose.component']._sync_overview_metrics()
        components = env['crose.component'].search_read(
            [],
            ['name', 'component_type', 'status']
        )
        stats = {
            'agents': env['fts.edge.node'].search_count([]),
            'instances': env['fts.nr.instance'].search_count([]),
            'topics': env['fts.mqtt.topic'].search_count([]),
        }
        metrics_param = env['ir.config_parameter'].sudo().get_param('feitas_iot.overview.metrics', '{}')
        metrics = {'cpu': '-', 'memory': '-', 'disk': '-', 'network': '-'}
        try:
            parsed = json.loads(metrics_param)
            if isinstance(parsed, dict):
                metrics.update(parsed)
        except Exception:
            pass
        metric_points = self._load_metrics_series_from_redis(env, time_range)
        if metric_points:
            last_point = metric_points[-1]
            metrics.update({
                'cpu': last_point.get('cpu', metrics['cpu']),
                'memory': last_point.get('memory', metrics['memory']),
                'disk': last_point.get('disk', metrics['disk']),
                'network': last_point.get('network', metrics['network']),
            })
        now_dt = fields.Datetime.to_datetime(fields.Datetime.now())
        minute_ago = fields.Datetime.to_string(now_dt - timedelta(minutes=1))
        day_start = fields.Datetime.to_string(now_dt.replace(hour=0, minute=0, second=0, microsecond=0))
        records_last_min = env['fts.data.log'].search_count([('create_date', '>=', minute_ago)])
        records_today = env['fts.data.log'].search_count([('create_date', '>=', day_start)])
        reports_today = env['fts.data.model'].search_count([
            ('write_date', '>=', day_start),
            ('spreadsheet_binary_data', '!=', False),
        ])
        throughput = {
            'records_per_sec': round(records_last_min / 60.0, 2),
            'records_today': records_today,
            'reports_today': reports_today,
            'latency_ms': env['ir.config_parameter'].sudo().get_param('feitas_iot.overview.latency_ms', '-'),
        }
        protocol = self._load_upload_activity_stats(env, time_range)
        industry_mode = env['ir.config_parameter'].sudo().get_param(
            'feitas_iot.overview.industry_mode', 'manufacturing'
        )
        if industry_mode == 'agriculture':
            kpis = [
                {'label': 'Greenhouse Environment Index', 'value': env['ir.config_parameter'].sudo().get_param('feitas_iot.overview.kpi_env_index', '82')},
                {'label': 'Soil Moisture Health', 'value': env['ir.config_parameter'].sudo().get_param('feitas_iot.overview.kpi_soil_moisture', '76%')},
                {'label': 'Irrigation Status', 'value': env['ir.config_parameter'].sudo().get_param('feitas_iot.overview.kpi_irrigation', 'Normal')},
            ]
            trend_title = 'Growth Trend'
        else:
            kpis = [
                {'label': 'OEE', 'value': env['ir.config_parameter'].sudo().get_param('feitas_iot.overview.kpi_oee', '85%')},
                {'label': 'Line Utilization', 'value': env['ir.config_parameter'].sudo().get_param('feitas_iot.overview.kpi_utilization', '88%')},
                {'label': 'Alarms Today', 'value': env['ir.config_parameter'].sudo().get_param('feitas_iot.overview.kpi_alarm', '3')},
            ]
            trend_title = 'Energy / Output Trend'
        trend_points_param = env['ir.config_parameter'].sudo().get_param(
            'feitas_iot.overview.trend_points',
            '[65,68,70,72,69,75,78,80,79,82,84,85]'
        )
        trend_points = []
        try:
            parsed_points = json.loads(trend_points_param)
            if isinstance(parsed_points, list):
                trend_points = [float(v) for v in parsed_points if isinstance(v, (int, float))]
        except Exception:
            trend_points = []
        if not trend_points:
            trend_points = [65, 68, 70, 72, 69, 75, 78, 80, 79, 82, 84, 85]
        online_devices = env['fts.edge.node'].search_count([('status', '=', 'online')])
        total_devices = env['fts.edge.node'].search_count([])
        offline_devices = max(total_devices - online_devices, 0)
        asset = {
            'devices_total': total_devices,
            'digital_models': env['fts.data.model'].search_count([]),
            'running_flows': env['instance.flow.line'].search_count([]),
            'commands_today': env['ir.config_parameter'].sudo().get_param('feitas_iot.overview.commands_today', '0'),
            'online_devices': online_devices,
            'offline_devices': offline_devices,
        }
        return {
            'components': components,
            'overview': {
                'stats': stats,
                'metrics': metrics,
                'dashboard': {
                    'connectivity': {
                        'protocol': protocol,
                    },
                    'throughput': throughput,
                    'metrics_trend': {
                        'time_range': time_range,
                        'points': metric_points,
                    },
                    'value_delivery': {
                        'industry_mode': industry_mode,
                        'kpis': kpis,
                        'trend_title': trend_title,
                        'trend_points': trend_points,
                    },
                    'asset_insight': asset,
                },
            }
        }
