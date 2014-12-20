# -*- coding: utf8 -*-
from unittest import TestCase

from mock import Mock, patch, call

from helga.plugins import webhooks
from twisted.web import server


@patch('helga.plugins.webhooks.registry')
def test_route(reg):
    reg.get_plugin.return_value = reg
    fake_fn = lambda: 'foo'
    webhooks.route('/foo', methods=['GET', 'POST'])(fake_fn)

    reg.add_route.assert_called_with(fake_fn, '/foo', ['GET', 'POST'])


@patch('helga.plugins.webhooks.registry')
def test_route_with_no_methods(reg):
    reg.get_plugin.return_value = reg
    fake_fn = lambda: 'foo'
    webhooks.route('/foo')(fake_fn)

    reg.add_route.assert_called_with(fake_fn, '/foo', ['GET'])


@patch('helga.plugins.webhooks.settings')
def test_authenticated_passes(settings):
    @webhooks.authenticated
    def fake_fn(*args, **kwargs):
        return 'OK'

    settings.WEBHOOKS_CREDENTIALS = [('foo', 'bar')]

    request = Mock()
    request.getUser.return_value = 'foo'
    request.getPassword.return_value = 'bar'

    assert fake_fn(request) == 'OK'


@patch('helga.plugins.webhooks.settings')
def test_authenticated_fails_when_called(settings):
    @webhooks.authenticated
    def fake_fn(*args, **kwargs):
        return 'OK'

    settings.WEBHOOKS_CREDENTIALS = [('person', 'password')]

    request = Mock()
    request.getUser.return_value = 'foo'
    request.getPassword.return_value = 'bar'

    assert fake_fn(request) == '401 Unauthorized'
    request.setResponseCode.assert_called_with(401)


class WebhookPluginTestCase(TestCase):

    def setUp(self):
        self.plugin = webhooks.WebhookPlugin()

    @patch('helga.plugins.webhooks.settings')
    def test_custom_port(self, settings):
        settings.WEBHOOKS_PORT = 1337
        plugin = webhooks.WebhookPlugin()
        assert self.plugin.port == 8080
        assert plugin.port == 1337

    @patch('helga.plugins.webhooks.settings')
    @patch('helga.plugins.webhooks.pkg_resources')
    def test_init_routes(self, pkg_resources, settings):
        settings.ENABLED_WEBHOOKS = None
        entry_points = [Mock()]
        pkg_resources.iter_entry_points.return_value = entry_points
        self.plugin._init_routes()
        pkg_resources.iter_entry_points.assert_called_with(group='helga_webhooks')
        assert entry_points[0].load.called

    @patch('helga.plugins.webhooks.settings')
    @patch('helga.plugins.webhooks.pkg_resources')
    def test_init_routes_ignores_not_enabled(self, pkg_resources, settings):
        settings.ENABLED_WEBHOOKS = ['foo']
        entry_points = [Mock(), Mock()]
        entry_points[0].name = 'foo'
        entry_points[1].name = 'bar'
        pkg_resources.iter_entry_points.return_value = entry_points
        self.plugin._init_routes()
        assert entry_points[0].load.called
        assert not entry_points[1].load.called

    @patch('helga.plugins.webhooks.reactor')
    def test_start(self, reactor):
        client = Mock()
        self.plugin._start(client)
        assert isinstance(self.plugin.root, webhooks.WebhookRoot)
        assert isinstance(self.plugin.site, server.Site)
        reactor.listenTCP.assert_called_with(8080, self.plugin.site)

    @patch('helga.plugins.webhooks.WebhookRoot')
    @patch('helga.plugins.webhooks.reactor')
    def test_start_with_existing_root(self, reactor, WebhookRoot):
        self.plugin.root = Mock()
        self.plugin._start(Mock())
        assert not WebhookRoot.called

    def test_stop(self):
        tcp_mock = Mock()
        self.plugin.tcp = tcp_mock
        self.plugin._stop()

        assert tcp_mock.stopListening.called
        assert tcp_mock.loseConnection.called
        assert self.plugin.tcp is None

    def test_list_routes(self):
        client = Mock()
        root = Mock()
        root.routes = {
            '/foo/bar/': [['POST', 'GET'], lambda: None],
            u'/unicode/support/☃': [['PUT'], lambda: None]
        }

        self.plugin.root = root
        self.plugin.list_routes(client, 'me')

        call_list = client.msg.call_args_list
        assert call('me', 'me, here are the routes I know about') in call_list
        assert call('me', '[POST,GET] /foo/bar/') in call_list
        assert call('me', u'[PUT] /unicode/support/☃') in call_list

    def test_control_stop(self):
        # When running
        self.plugin.tcp = 'foo'
        with patch.object(self.plugin, '_stop') as stop:
            assert self.plugin.control('stop') == 'Webhooks service stopped'
            assert stop.called

        # When not running
        self.plugin.tcp = None
        with patch.object(self.plugin, '_stop') as stop:
            assert self.plugin.control('stop') == 'Webhooks service not running'
            assert not stop.called

    def test_control_start(self):
        # When running
        self.plugin.tcp = 'foo'
        with patch.object(self.plugin, '_start') as start:
            assert self.plugin.control('start') == 'Webhooks service already running'
            assert not start.called

        # When not running
        self.plugin.tcp = None
        with patch.object(self.plugin, '_start') as start:
            assert self.plugin.control('start') == 'Webhooks service started'
            assert start.called

    def test_run_defaults_to_list_routes(self):
        client = Mock()
        with patch.object(self.plugin, 'list_routes') as routes:
            self.plugin.run(client, '#bots', 'me', 'msg', 'cmd', [])
            client.me.assert_called_with('#bots', 'whispers to me')
            routes.assert_called_with(client, 'me')

    def test_run_list_routes(self):
        client = Mock()
        with patch.object(self.plugin, 'list_routes') as routes:
            self.plugin.run(client, '#bots', 'me', 'msg', 'cmd', ['routes'])
            client.me.assert_called_with('#bots', 'whispers to me')
            routes.assert_called_with(client, 'me')

    def test_run_start_stop_requires_operator(self):
        client = Mock(operators=[])
        with patch.object(self.plugin, 'control') as control:
            resp = self.plugin.run(client, '#bots', 'me', 'msg', 'cmd', ['start'])
            assert resp == 'Sorry me, Only an operator can do that'
            assert not control.called

            resp = self.plugin.run(client, '#bots', 'me', 'msg', 'cmd', ['stop'])
            assert resp == 'Sorry me, Only an operator can do that'
            assert not control.called

    def test_run_start_stop_as_operator(self):
        client = Mock(operators=['me'])
        with patch.object(self.plugin, 'control') as control:
            self.plugin.run(client, '#bots', 'me', 'msg', 'cmd', ['start'])
            control.assert_called_with('start')

            control.reset_mock()
            self.plugin.run(client, '#bots', 'me', 'msg', 'cmd', ['stop'])
            control.assert_called_with('stop')


class WebhookRootTestCase(TestCase):

    def setUp(self):
        self.client = Mock()
        self.root = webhooks.WebhookRoot(self.client)

    def test_render_returns_404(self):
        mock_fn = Mock(return_value='foo')
        request = Mock(path='/foo/bar/baz', method='POST')
        self.root.routes['/path/to/resource'] = (['GET'], mock_fn)

        assert '404 Not Found' == self.root.render(request)
        request.setResponseCode.assert_called_with(404)

    def test_render_returns_405(self):
        mock_fn = Mock(return_value='foo')
        request = Mock(path='/path/to/resource', method='POST')
        self.root.routes['/path/to/resource'] = (['GET'], mock_fn)

        assert '405 Method Not Allowed' == self.root.render(request)
        request.setResponseCode.assert_called_with(405)

    def test_render(self):
        mock_fn = Mock(return_value='foo')
        request = Mock(path='/path/to/resource', method='GET')
        self.root.routes['/path/to/resource'] = (['GET'], mock_fn)

        assert 'foo' == self.root.render(request)
        mock_fn.assert_called_with(request, self.root.irc_client)
        request.setHeader.assert_called_with('Server', 'helga')

    def test_reunder_handles_http_error(self):
        mock_fn = Mock(side_effect=webhooks.HttpError(404, 'foo not found'))
        request = Mock(path='/path/to/resource', method='GET')
        self.root.routes['/path/to/resource'] = (['GET'], mock_fn)

        assert 'foo not found' == self.root.render(request)
        request.setResponseCode.assert_called_with(404)

    def test_add_route(self):
        fn = lambda: None
        methods = ['GET', 'POST']
        path = '/path/to/resource'
        self.root.add_route(fn, path, methods)
        assert self.root.routes[path] == (methods, fn)
