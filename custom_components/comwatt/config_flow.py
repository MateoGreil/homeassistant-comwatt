import voluptuous as vol
from homeassistant import config_entries, core
from .const import DOMAIN
from comwatt_client import ComwattClient
import asyncio

class ComwattConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            username = user_input["username"]
            password = user_input["password"]

            # Call the _authenticate method in a separate thread using asyncio.to_thread
            # This allows blocking calls to be executed without blocking the event loop
            # and ensures the stability of the application.
            cwt_session = await asyncio.to_thread(lambda: self._authenticate(username, password))

            if cwt_session is not None:
                # Save the user_input and cookie in the entry configuration
                return self.async_create_entry(title="Comwatt", data={"username": username, "password": password, "cwt_session": cwt_session.value})
            else:
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema(
                        {
                            vol.Required("username"): str,
                            vol.Required("password"): str,
                        }
                    ),
                    errors={"base": "Authentication failed"},
                )

        # Show the user configuration form
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("username"): str,
                    vol.Required("password"): str,
                }
            ),
        )

    def _authenticate(self, username, password):
        # Perform authentication using the Comwatt client
        # Return cwt_session if authentication succeeds, None otherwise

        client = ComwattClient()
        try:
            client.authenticate(username, password)

            cwt_session = None
            for cookie in client.session.cookies:
                if cookie.name == 'cwt_session':
                    cwt_session = cookie
                    break

            return cwt_session
        except Exception:
            return None
