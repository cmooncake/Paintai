"""Run in the torch environment; the lightweight app environment skips these tests."""

import copy
import importlib.util
import runpy
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("torch"), "Requires the local LoRA torch environment")
class CheckpointStateTests(unittest.TestCase):
    def test_checkpoint_export_preserves_subsequent_training(self):
        import torch

        guard = runpy.run_path(str(Path(__file__).parents[1] / "scripts/train-style-lora.py"))[
            "checkpoint_guard"
        ]

        def train(with_checkpoints):
            torch.manual_seed(42)
            model = torch.nn.Linear(3, 2)
            optimizer = torch.optim.AdamW(model.parameters(), lr=5e-5)
            for step in range(8):
                model(torch.randn(4, 3)).square().mean().backward()
                optimizer.step()
                optimizer.zero_grad(set_to_none=True)
                if with_checkpoints and step in [1, 3]:
                    with guard(torch, model, optimizer):
                        model.eval()
                        torch.rand(100)
                        model.load_state_dict(copy.deepcopy(model.state_dict()))
                    self.assertTrue(model.training)
            return model.state_dict(), optimizer.state_dict(), torch.get_rng_state()

        expected, expected_optimizer, expected_rng = train(False)
        actual, actual_optimizer, actual_rng = train(True)
        self.assertTrue(torch.equal(expected_rng, actual_rng))
        for key in expected:
            self.assertTrue(torch.equal(expected[key], actual[key]))
        for parameter, state in expected_optimizer["state"].items():
            for key, value in state.items():
                self.assertTrue(torch.equal(value, actual_optimizer["state"][parameter][key]))

    def test_replacing_optimizer_parameters_is_rejected(self):
        import torch

        guard = runpy.run_path(str(Path(__file__).parents[1] / "scripts/train-style-lora.py"))[
            "checkpoint_guard"
        ]
        model = torch.nn.Linear(3, 2)
        optimizer = torch.optim.AdamW(model.parameters())
        with self.assertRaisesRegex(ValueError, "optimizer-owned"):
            with guard(torch, model, optimizer):
                model.weight = torch.nn.Parameter(model.weight.detach().clone())


if __name__ == "__main__":
    unittest.main()
