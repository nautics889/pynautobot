"""
(c) 2017 DigitalOcean

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

This file has been modified by NetworktoCode, LLC.
"""
try:
    import concurrent.futures as cf
except ImportError:
    pass
import json
import requests


def calc_pages(limit, count):
    """Calculate number of pages required for full results set."""
    return int(count / limit) + (limit % count > 0)


class RequestError(Exception):
    """Basic Request Exception

    More detailed exception that returns the original requests object
    for inspection. Along with some attributes with specific details
    from the requests object. If return is json we decode and add it
    to the message.

    :Example:

    >>> try:
    ...   nb.dcim.devices.create(name="destined-for-failure")
    ... except pynautobot.RequestError as e:
    ...   print(e.error)

    """

    def __init__(self, message):
        req = message

        if req.status_code == 404:
            message = "The requested url: {} could not be found.".format(req.url)
        else:
            try:
                message = "The request failed with code {} {}: {}".format(req.status_code, req.reason, req.json())
            except ValueError:
                message = (
                    "The request failed with code {} {} but more specific "
                    "details were not returned in json. Check the Nautobot Logs "
                    "or investigate this exception's error attribute.".format(req.status_code, req.reason)
                )

        super(RequestError, self).__init__(message)
        self.req = req
        self.request_body = req.request.body
        self.base = req.url
        self.error = req.text


class RequestErrorFromException(Exception):
    """RequestErrorFromException is raised from exception."""


class AllocationError(Exception):
    """Allocation Exception

    Used with available-ips/available-prefixes when there is no
    room for allocation and Nautobot returns 204 No Content.
    """

    def __init__(self, message):
        req = message

        message = "The requested allocation could not be fulfilled."

        super(AllocationError, self).__init__(message)
        self.req = req
        self.request_body = req.request.body
        self.base = req.url
        self.error = message


class ContentError(Exception):
    """Content Exception

    If the API URL does not point to a valid Nautobot API, the server may
    return a valid response code, but the content is not json. This
    exception is raised in those cases.
    """

    def __init__(self, message):
        req = message

        message = "The server returned invalid (non-json) data. Maybe not " "a Nautobot server?"

        super(ContentError, self).__init__(message)
        self.req = req
        self.request_body = req.request.body
        self.base = req.url
        self.error = message


class Request(object):
    """Creates requests to the Nautobot API

    Responsible for building the url and making the HTTP(S) requests to
    Nautobot's API

    :param base: (str) Base URL passed in api() instantiation.
    :param filters: (dict, optional) contains key/value pairs that
        correlate to the filters a given endpoint accepts.
        In (e.g. /api/dcim/devices/?name='test') 'name': 'test'
        would be in the filters dict.
    :param int,optional max_workers: Set the maximum workers for threading in ``.all()``
        and ``.filter()`` requests.
    """

    def __init__(
        self,
        base,
        http_session,
        filters=None,
        key=None,
        token=None,
        threading=False,
        max_workers=4,
        api_version=None,
    ):
        """
        Instantiates a new Request object

        Args:
            base (string): Base URL passed in api() instantiation.
            filters (dict, optional): contains key/value pairs that
                correlate to the filters a given endpoint accepts.
                In (e.g. /api/dcim/devices/?name='test') 'name': 'test'
                would be in the filters dict.
            key (int, optional): database id of the item being queried.
            api_version (str, optional): Set to override the default Nautobot REST API Version.
        """
        self.base = self.normalize_url(base)
        self.filters = filters
        self.key = key
        self.token = token
        self.http_session = http_session
        self.url = self.base if not key else "{}{}/".format(self.base, key)
        self.threading = threading
        self.max_workers = max_workers
        self.api_version = api_version

    def get_openapi(self):
        """Gets the OpenAPI Spec"""
        headers = {
            "Content-Type": "application/json;",
        }

        if self.api_version:
            headers["accept"] = f"application/json; version={self.api_version}"

        try:
            req = self.http_session.get(
                "{}docs/?format=openapi".format(self.normalize_url(self.base)),
                headers=headers,
            )
        except requests.exceptions.RetryError as error:
            raise RequestErrorFromException from error

        if req.ok:
            return req.json()
        else:
            raise RequestError(req)

    def get_version(self):
        """Gets the API version of Nautobot.

        Issues a GET request to the base URL to read the API version from the
        response headers.

        :Raises: RequestError if req.ok returns false.
        :Returns: Version number as a string. Empty string if version is not
        present in the headers.
        """
        headers = {"Content-Type": "application/json;"}
        if self.api_version:
            headers["accept"] = f"application/json; version={self.api_version}"

        try:
            req = self.http_session.get(
                self.normalize_url(self.base),
                headers=headers,
            )
        except requests.exceptions.RetryError as error:
            raise RequestErrorFromException from error

        if req.ok:
            return req.headers.get("API-Version", "")
        else:
            raise RequestError(req)

    def get_status(self):
        """Gets the status from /api/status/ endpoint in Nautobot.

        :Returns: Dictionary as returned by Nautobot.
        :Raises: RequestError if request is not successful.
        """
        headers = {"Content-Type": "application/json;"}
        if self.token:
            headers["authorization"] = "Token {}".format(self.token)

        if self.api_version:
            headers["accept"] = f"application/json; version={self.api_version}"

        try:
            req = self.http_session.get(
                "{}status/".format(self.normalize_url(self.base)),
                headers=headers,
            )
        except requests.exceptions.RetryError as error:
            raise RequestErrorFromException from error

        if req.ok:
            return req.json()
        else:
            raise RequestError(req)

    def normalize_url(self, url):
        """Builds a url for POST actions."""
        if url[-1] != "/":
            return "{}/".format(url)

        return url

    def _make_call(self, verb="get", url_override=None, add_params=None, data=None):
        if verb in ("post", "put") or (verb in ("delete") and data):
            headers = {"Content-Type": "application/json;"}
        else:
            headers = {"accept": "application/json;"}

        if self.token:
            headers["authorization"] = "Token {}".format(self.token)

        if self.api_version:
            headers["accept"] = f"application/json; version={self.api_version}"

        params = {}
        if not url_override:
            if self.filters:
                params.update(self.filters)
            if add_params:
                params.update(add_params)

        try:
            req = getattr(self.http_session, verb)(url_override or self.url, headers=headers, params=params, json=data)
        except requests.exceptions.RetryError as error:
            raise RequestErrorFromException from error

        if req.status_code == 204 and verb == "post":
            raise AllocationError(req)
        if verb == "delete":
            if req.ok:
                return True
            else:
                raise RequestError(req)
        elif req.ok:
            try:
                return req.json()
            except json.JSONDecodeError:
                raise ContentError(req)
        else:
            raise RequestError(req)

    def concurrent_get(self, ret, page_size, page_offsets):
        futures_to_results = []
        with cf.ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for offset in page_offsets:
                new_params = {"offset": offset, "limit": page_size}
                futures_to_results.append(pool.submit(self._make_call, add_params=new_params))

            for future in cf.as_completed(futures_to_results):
                result = future.result()
                ret.extend(result["results"])

    def get(self, add_params=None):
        """Makes a GET request.

        Makes a GET request to Nautobot's API, and automatically recurses
        any paginated results.

        :raises: RequestError if req.ok returns false.
        :raises: ContentError if response is not json.

        :Returns: List of `Response` objects returned from the
            endpoint.
        """

        def req_all():
            req = self._make_call(add_params=add_params)
            if isinstance(req, dict) and req.get("results") is not None:
                ret = req["results"]
                first_run = True
                while req["next"]:
                    # Not worrying about making sure add_params kwargs is
                    # passed in here because results from detail routes aren't
                    # paginated, thus far.
                    if first_run:
                        req = self._make_call(add_params={"limit": req["count"], "offset": len(req["results"])})
                    else:
                        req = self._make_call(url_override=req["next"])
                    first_run = False
                    ret.extend(req["results"])
                return ret
            else:
                return req

        def req_all_threaded(add_params):
            if add_params is None:
                # Limit must be 0 to discover the max page size
                add_params = {"limit": 0}
            req = self._make_call(add_params=add_params)
            if isinstance(req, dict) and req.get("results") is not None:
                ret = req["results"]
                if req.get("next"):
                    page_size = len(req["results"])
                    pages = calc_pages(page_size, req["count"])
                    page_offsets = [increment * page_size for increment in range(1, pages)]
                    if pages == 1:
                        req = self._make_call(url_override=req.get("next"))
                        ret.extend(req["results"])
                    else:
                        self.concurrent_get(ret, page_size, page_offsets)

                return ret
            else:
                return req

        if self.threading:
            return req_all_threaded(add_params)

        return req_all()

    def put(self, data):
        """Makes PUT request.

        Makes a PUT request to Nautobot's API.

        :param data: (dict) Contains a dict that will be turned into a
            json object and sent to the API.
        :raises: RequestError if req.ok returns false.
        :raises: ContentError if response is not json.
        :returns: Dict containing the response from Nautobot's API.
        """
        return self._make_call(verb="put", data=data)

    def post(self, data):
        """Makes POST request.

        Makes a POST request to Nautobot's API.

        :param data: (dict) Contains a dict that will be turned into a
            json object and sent to the API.
        :raises: RequestError if req.ok returns false.
        :raises: AllocationError if req.status_code is 204 (No Content)
            as with available-ips and available-prefixes when there is
            no room for the requested allocation.
        :raises: ContentError if response is not json.
        :Returns: Dict containing the response from Nautobot's API.
        """
        return self._make_call(verb="post", data=data)

    def delete(self, data=None):
        """Makes DELETE request.

        Makes a DELETE request to Nautobot's API.

        :param data: (list) Contains a dict that will be turned into a
            json object and sent to the API.
        Returns:
            True if successful.

        Raises:
            RequestError if req.ok doesn't return True.
        """
        return self._make_call(verb="delete", data=data)

    def patch(self, data):
        """Makes PATCH request.

        Makes a PATCH request to Nautobot's API.

        :param data: (dict) Contains a dict that will be turned into a
            json object and sent to the API.
        :raises: RequestError if req.ok returns false.
        :raises: ContentError if response is not json.
        :returns: Dict containing the response from Nautobot's API.
        """
        return self._make_call(verb="patch", data=data)

    def options(self):
        """Makes an OPTIONS request.

        Makes an OPTIONS request to Nautobot's API.

        :raises: RequestError if req.ok returns false.
        :raises: ContentError if response is not json.

        :returns: Dict containing the response from Nautobot's API.
        """
        return self._make_call(verb="options")

    def get_count(self, *args, **kwargs):
        """Returns object count for query

        Makes a query to the endpoint with ``limit=1`` set and only
        returns the value of the "count" field.

        :raises: RequestError if req.ok returns false.
        :raises: ContentError if response is not json.

        :returns: Int of number of objects query returned.
        """

        return self._make_call(add_params={"limit": 1})["count"]
