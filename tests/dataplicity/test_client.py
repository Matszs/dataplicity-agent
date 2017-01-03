import pytest
from dataplicity import client as mclient
from mock import patch


@pytest.fixture
def serial_file(tmpdir):
    serial_file = tmpdir.join("serial")
    serial_file.write("test-serial")
    with patch('dataplicity.constants.SERIAL_LOCATION', str(serial_file)):
        yield str(serial_file)


@pytest.fixture
def auth_file(tmpdir):
    auth_file = tmpdir.join("auth")
    auth_file.write("test-auth")
    with patch('dataplicity.constants.AUTH_LOCATION', str(auth_file)):
        yield str(auth_file)


def test_client_initialization(auth_file, serial_file):
    """ this function tests 'succesful' initialization of the client
    """
    mclient.Client()


def test_client_unsuccesful_init(tmpdir):
    """ the client won't start if the file is missing.
        serial file is read first, so we have to fake the location there in
        order to raise IOError.
    """
    non_existing_path = tmpdir.join("non-existing-file")
    with patch(
        'dataplicity.constants.SERIAL_LOCATION', str(non_existing_path)
    ):
        with pytest.raises(IOError):
            mclient.Client()


def test_system_exit_call(serial_file, auth_file, mocker):
    """ test client initialization with error handling
    """
    client = mclient.Client()

    def poll_which_raises(self):
        raise SystemExit

    def poll_which_raises_keyboardint(self):
        raise KeyboardInterrupt

    # this attaches to client.close() method which should be called at the end
    # of run_forever. The method won't be monkeypatched, but we'll be able
    # to check whether the method was called or not.
    mocker.spy(client, 'close')

    with patch('dataplicity.client.Client.poll', poll_which_raises):
        client.run_forever()
        assert client.close.call_count == 1

    with patch(
        'dataplicity.client.Client.poll', poll_which_raises_keyboardint
    ):
        client.run_forever()
        assert client.close.call_count == 2
