from abc import ABC, abstractmethod
from typing import Any


class BaseAgent(ABC):
    """Contrato mínimo que deben cumplir todos los subagentes."""

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Ejecuta la tarea principal del subagente y devuelve el resultado."""
        ...
