"""
Configuration management for 2D23D.

Loads layer mappings and classification rules from JSON files.
Following constitutional principle: Configuration Over Code.
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from fnmatch import fnmatch
from loguru import logger


class Config:
    """Configuration manager for layer mappings and classification rules."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration.

        Args:
            config_path: Path to JSON config file. If None, uses default AIA config.
        """
        if config_path is None:
            # Use default AIA config
            config_path = Path(__file__).parent.parent.parent / "config" / "layer_mapping_aia.json"

        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from JSON file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, 'r') as f:
            self._config = json.load(f)

        logger.info(f"Loaded config: {self._config.get('name', 'Unknown')}")

    def get_layers_for_element(self, element_type: str) -> List[str]:
        """
        Get layer patterns for a specific element type.

        Args:
            element_type: Type of element ('walls', 'columns', 'grid', etc.)

        Returns:
            List of layer name patterns
        """
        mapping = self._config.get("layer_mapping", {}).get(element_type, {})
        return mapping.get("patterns", [])

    def get_excluded_layers_for_element(self, element_type: str) -> List[str]:
        """
        Get excluded layer patterns for a specific element type.

        Args:
            element_type: Type of element

        Returns:
            List of excluded layer patterns
        """
        mapping = self._config.get("layer_mapping", {}).get(element_type, {})
        return mapping.get("exclude", [])

    def matches_layer_pattern(self, layer_name: str, element_type: str) -> bool:
        """
        Check if a layer name matches the patterns for an element type.

        Args:
            layer_name: Name of the layer to check
            element_type: Type of element

        Returns:
            True if layer matches and is not excluded
        """
        patterns = self.get_layers_for_element(element_type)
        excluded = self.get_excluded_layers_for_element(element_type)

        # Check exclusions first
        for exclude_pattern in excluded:
            if fnmatch(layer_name.upper(), exclude_pattern.upper()):
                return False

        # Check matches
        for pattern in patterns:
            if fnmatch(layer_name.upper(), pattern.upper()):
                return True

        return False

    def get_classification_rule(self, element_type: str, rule_name: str, default: Any = None) -> Any:
        """
        Get a classification rule value.

        Args:
            element_type: Type of element ('wall_detection', 'column_detection', etc.)
            rule_name: Name of the rule
            default: Default value if rule not found

        Returns:
            Rule value or default
        """
        rules = self._config.get("classification_rules", {}).get(element_type, {})
        return rules.get(rule_name, default)

    def get_geometry_default(self, param_name: str, default: Any = None) -> Any:
        """
        Get a geometry default parameter.

        Args:
            param_name: Parameter name
            default: Default value if not found

        Returns:
            Parameter value or default
        """
        return self._config.get("geometry_defaults", {}).get(param_name, default)


# Global default config instance
_default_config: Optional[Config] = None


def get_default_config() -> Config:
    """Get the default global configuration instance."""
    global _default_config
    if _default_config is None:
        _default_config = Config()
    return _default_config


def load_config(config_path: str) -> Config:
    """
    Load configuration from a specific file.

    Args:
        config_path: Path to JSON config file

    Returns:
        Config instance
    """
    return Config(config_path)
