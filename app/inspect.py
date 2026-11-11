from pathlib import Path
from app.openapi import operation_names,read_document
print(operation_names(read_document("fixtures/v1.json")))
