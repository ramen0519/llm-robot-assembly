"""Validate configurable frames and real part/table settling without a grasp."""

import copy
from pathlib import Path
import sys
import tempfile
import unittest

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from view_workcell import check_workcell
from workcell_scene import (DEFAULT_CONFIG, TABLE_GEOM, build_workcell, load_config,
                            observe_workcell, part_name, target_name, validate_config)


class WorkcellTests(unittest.TestCase):
    def test_default_part_settles_through_contact_without_attachment(self):
        config = load_config()
        model, data = build_workcell(config)
        initial_z = float(data.body(part_name(config)).xpos[2])
        free = model.joint(part_name(config) + "_free")
        self.assertEqual(model.jnt_type[free.id], mujoco.mjtJoint.mjJNT_FREE)
        self.assertEqual((model.nq, model.nv, model.nu, model.neq), (21, 20, 7, 3))
        # Upstream equalities are unnamed: check their actual constrained objects, not labels.
        for i in range(model.neq):
            self.assertIn(model.eq_type[i], (mujoco.mjtEq.mjEQ_CONNECT, mujoco.mjtEq.mjEQ_JOINT))
            lookup = model.joint if model.eq_type[i] == mujoco.mjtEq.mjEQ_JOINT else model.body
            for object_id in (model.eq_obj1id[i], model.eq_obj2id[i]):
                self.assertTrue(lookup(int(object_id)).name.startswith("gripper_"))
        report = check_workcell(model, data, config)
        self.assertEqual(report["status"], "success")
        self.assertAlmostEqual(data.time, 3.0)
        self.assertAlmostEqual(report["first_support_s"], 0.032)
        self.assertGreater(initial_z - data.body(part_name(config)).xpos[2], 0.004)
        self.assertLess(report["final_window_maxima"]["height_error_m"], 0.001)
        self.assertGreater(data.ncon, 0)
        self.assertEqual(report["observation"]["source"], "sim_ground_truth")
        self.assertEqual(report["observation"]["target"]["source"], "configured_target")
        self.assertEqual(report["observation"]["part"]["id"], "part_a")
        np.testing.assert_array_equal(model.body_gravcomp, 0)
        np.testing.assert_array_equal(data.xfrc_applied, 0)
        np.testing.assert_array_equal(data.qfrc_applied, 0)

    def test_translated_rotated_taller_table_and_robot_use_the_declared_frames(self):
        config = load_config()
        config["table"].update(top_center_world_m=[0.4, -0.2, 1.05], size_xy_m=[1.2, 0.9], yaw_deg=90.0)
        config["robot"].update(base_xy_table_m=[-0.10, 0.20], yaw_deg=90.0, mount_height_m=0.03)
        config["part"].update(size_m=[0.05, 0.035, 0.06], mass_kg=0.08, yaw_deg=25.0)
        config["target"]["yaw_deg"] = -30.0
        before = copy.deepcopy(config)
        model, data = build_workcell(config)
        # Independently computed for +90deg table yaw: world(x,y) = origin + (-local_y, local_x).
        np.testing.assert_allclose(data.body("base").xpos, [0.2, -0.3, 1.08], atol=1e-12)
        np.testing.assert_allclose(data.geom("workcell_robot_mount").xpos, [0.2, -0.3, 1.065], atol=1e-12)
        np.testing.assert_allclose(data.body("base").xmat.reshape(3, 3), np.diag([-1, -1, 1]), atol=1e-12)
        np.testing.assert_allclose(data.body(part_name(config)).xpos, [0.5, -0.07, 1.085], atol=1e-12)
        np.testing.assert_allclose(data.site(target_name(config)).xpos, [0.52, -0.32, 1.051], atol=1e-12)
        np.testing.assert_allclose(data.geom(TABLE_GEOM).xmat.reshape(3, 3),
                                   [[0, -1, 0], [1, 0, 0], [0, 0, 1]], atol=1e-12)
        initial = observe_workcell(model, data, config)
        expected_yaw = np.deg2rad(115)
        np.testing.assert_allclose(initial["part"]["quaternion_xyzw"], [0, 0, np.sin(expected_yaw / 2), np.cos(expected_yaw / 2)], atol=1e-12)
        np.testing.assert_allclose(initial["target"]["desired_part_center_m"], [0.52, -0.32, 1.08], atol=1e-12)
        np.testing.assert_allclose(initial["target"]["desired_quaternion_xyzw"],
                                   [0, 0, 0.5, np.sqrt(3) / 2], atol=1e-12)
        report = check_workcell(model, data, config)
        self.assertEqual(report["status"], "success")
        np.testing.assert_allclose(report["observation"]["part"]["position_table_m"], [0.13, -0.10, 0.03], atol=0.001)
        self.assertEqual(config, before)  # The builder never rewrites user's measured configuration.

    def test_floating_stationary_part_without_support_is_not_success(self):
        config = load_config()
        model, data = build_workcell(config)
        model.opt.gravity[:] = 0  # Test intervention: quiet but unsupported is not settled-on-table.
        report = check_workcell(model, data, config)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["checks"]["supported_in_entire_final_window"])
        self.assertIsNone(report["first_support_s"])
        self.assertGreater(report["final_window_maxima"]["height_error_m"], 0.004)
        self.assertLess(report["final_window_maxima"]["linear_speed_m_s"], 1e-10)

    def test_part_overlapping_gripper_is_rejected_before_simulation_advances(self):
        config = load_config()
        config["part"]["size_m"][2] = 0.30
        model, data = build_workcell(config)
        self.assertGreater(data.ncon, 0)  # Real collision calculation, no mock contact array.
        with self.assertRaisesRegex(RuntimeError, "Unexpected workcell contact"):
            check_workcell(model, data, config)
        self.assertEqual(data.time, 0)

    def test_invalid_layout_and_unsupported_hardware_are_rejected(self):
        mutations = [
            ("table", "size_xy_m", [-1, 0.8]),
            ("table", "top_center_world_m", [0, 0, 0.01]),
            ("part", "mass_kg", float("nan")),
            ("part", "spawn_xy_table_m", [0.499, 0]),
            ("target", "xy_table_m", [0, 0.399]),
            ("robot", "base_xy_table_m", [10, 0]),
            ("robot", "model", "ur3"),
            ("robot", "mount_height_m", -0.01),
            ("part", "shape", "mesh"),
            ("contact", "friction", [0.8, -1, 0]),
        ]
        for section, field, value in mutations:
            with self.subTest(section=section, field=field):
                config = load_config()
                config[section][field] = value
                with self.assertRaises(ValueError):
                    build_workcell(config)
        config = load_config()
        config["robot"]["base_position"] = [0, 0, 0]  # Ambiguous frame / unknown key.
        with self.assertRaisesRegex(ValueError, "exactly these fields"):
            validate_config(config)

    def test_rotated_footprint_and_config_file_override(self):
        config = load_config()
        config["part"].update(size_m=[0.05, 0.05, 0.04], spawn_xy_table_m=[0.47, 0], yaw_deg=45.0)
        with self.assertRaisesRegex(ValueError, "rotated part footprint"):
            validate_config(config)
        with tempfile.TemporaryDirectory() as folder:
            custom = Path(folder) / "my_workcell.toml"
            source = DEFAULT_CONFIG.read_text(encoding="utf-8").replace(
                'top_center_world_m = [0.0, 0.0, 0.75]', 'top_center_world_m = [0.3, -0.2, 0.90]')
            custom.write_text(source, encoding="utf-8-sig")
            loaded = load_config(custom)
            model, data = build_workcell(loaded)
            np.testing.assert_allclose(data.body("base").xpos, [0.3, 0.0, 0.9])
            np.testing.assert_allclose(data.geom(TABLE_GEOM).xpos, [0.3, -0.2, 0.88])


if __name__ == "__main__":
    unittest.main()
