# Argumentation Microservices

This project contains Protobuf definitions for building complex argumentation machines.
The idea is to facilitate a microservice-oriented architecture where individual parts can be swapped out easily.
Along with the Protobuf files, the project also contains code for generating libraries for multiple programming languages that are released as packages in their respective registries:

- [Python](https://pypi.org/project/arg-services/)
- [TypeScript](https://www.npmjs.com/package/arg-services)

Documentation can be found at the [Buf Schema Registry](https://buf.build/recap/arg-services).

## Build Locally

`buf generate && uv build`

## Runtimes

All messages (`*_pb2.py`) use the official [`protobuf`](https://pypi.org/project/protobuf/) runtime.
Install the extra for the RPC runtime you want to use, both share the same messages:

| Extra | Install | Services | Helpers |
| --- | --- | --- | --- |
| `grpcio` | `uv add arg-services[grpcio]` | `*_pb2_grpc.py` ([`grpcio`](https://pypi.org/project/grpcio/)) | `arg_services.grpcio` |
| `connectrpc` | `uv add arg-services[connectrpc]` | `*_connect.py` ([`connectrpc`](https://connectrpc.com/docs/python/getting-started/)) | `arg_services.connectrpc` |

### Validation

`arg_services.validation` checks requests independently of the runtime and raises a `ValidationError`.
Add the `ValidationInterceptor` of your runtime to report it as `INVALID_ARGUMENT`, e.g., `serve(..., interceptors=[ValidationInterceptor()])`.
To pass the traceback of unhandled exceptions to gRPC clients, also add `arg_services.grpcio.ExceptionInterceptor()`.

```python
from arg_services.validation import require_all

require_all(["texts", "config.language"], request)
require_all(["text"], *request.items)
```

### Connect

Connect services are plain ASGI/WSGI applications that also speak gRPC, so existing gRPC clients keep working.
gRPC clients and reflection need a server with HTTP/2 trailers such as [pyvoy](https://pyvoy.dev), Connect clients work with any server such as uvicorn or gunicorn.
Since `connectrpc-grpcreflect` only supports protobuf-py, use `arg_services.connectrpc.reflection_app` to serve gRPC reflection.

```python
from arg_services.connectrpc import ValidationInterceptor, reflection_app
from arg_services.nlp.v1 import nlp_pb2
from arg_services.nlp.v1.nlp_connect import NlpService, NlpServiceASGIApplication
from arg_services.validation import require_all
from connectrpc.request import RequestContext
from starlette.applications import Starlette
from starlette.routing import Mount


class Nlp(NlpService):
    async def vectors(
        self, request: nlp_pb2.VectorsRequest, ctx: RequestContext
    ) -> nlp_pb2.VectorsResponse:
        require_all(["texts"], request)

        return nlp_pb2.VectorsResponse()


nlp_app = NlpServiceASGIApplication(Nlp(), interceptors=[ValidationInterceptor()])
reflection = reflection_app(nlp_app)
app = Starlette(
    routes=[Mount(nlp_app.path, nlp_app), Mount(reflection.path, reflection)]
)
```
