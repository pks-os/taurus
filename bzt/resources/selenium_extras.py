# Utility functions and classes for Taurus Selenium tests

from selenium.common.exceptions import NoSuchWindowException, NoSuchFrameException, NoSuchElementException, \
    TimeoutException
from apiritif import get_transaction_handlers, set_transaction_handlers, get_from_thread_store, get_iteration
from selenium.webdriver.common.by import By
import time

from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as econd


def _get_driver():
    return get_from_thread_store("driver")


def _get_timeout():
    timeout = get_from_thread_store("timeout")
    if not (timeout or timeout == 0):   # timeout in (None, []), default requires
        timeout = 30

    return timeout


def add_flow_markers():
    handlers = get_transaction_handlers()
    handlers["enter"].append(_send_start_flow_marker)
    handlers["exit"].append(_send_exit_flow_marker)
    set_transaction_handlers(handlers)


def _send_marker(stage, params):
    _get_driver().execute_script("/* FLOW_MARKER test-case-%s */" % stage, params)


def _send_start_flow_marker(*args, **kwargs):   # for apiritif. remove when compatibiltiy code in
    stage = "start"                             # apiritif removed (http.py) and apiritif released ( > 0.9.2)

    test_case, test_suite, scenario_name, data_sources = get_from_thread_store(
        ['test_case', 'test_suite', 'scenario_name', 'data_sources']
    )
    params = {
        "testCaseName": test_case,
        "testSuiteName": scenario_name or test_suite}

    if data_sources:
        params["testDataIterationId"] = get_iteration()

    _send_marker(stage, params)


def _send_exit_flow_marker(*args, **kwargs):   # for apiritif. remove when compatibiltiy code in
    stage = "stop"                             # apiritif removed (http.py) and apiritif released ( > 0.9.2)
    labels = "status", "message"
    values = get_from_thread_store(labels)
    params = dict(zip(labels, values))
    _send_marker(stage, params)


class FrameManager:
    def __init__(self, driver):
        self.driver = driver

    def switch(self, frame_name=None):
        try:
            if not frame_name or frame_name == "relative=top":
                self.driver.switch_to_default_content()
            elif frame_name.startswith("index="):  # Switch using index frame using relative position
                self.driver.switch_to.frame(int(frame_name.split("=")[1]))
            elif frame_name == "relative=parent":  # Switch to parent frame of the current frame
                self.driver.switch_to.parent_frame()
            else:  # Use the selenium alternative
                self.driver.switch_to.frame(frame_name)
        except NoSuchFrameException:
            raise NoSuchFrameException("Invalid Frame ID: %s" % frame_name)


class WindowManager:
    def __init__(self, driver):
        self.driver = driver
        self.windows = {}

    def switch(self, window_name=None):
        try:
            if not window_name:  # Switch to last window created
                self.driver.switch_to.window(self.driver.window_handles[-1])
            else:
                if window_name.isdigit():  # Switch to window handler index
                    self._switch_by_idx(int(window_name))
                else:
                    if window_name.startswith("win_ser_"):  # Switch using window sequential mode
                        self._switch_by_win_ser(window_name)
                    else:  # Switch using window name
                        self.driver.switch_to.window(window_name)
        except NoSuchWindowException:
            raise NoSuchWindowException("Invalid Window ID: %s" % window_name)

    def _switch_by_idx(self, win_index):
        wnd_handlers = self.driver.window_handles
        if len(wnd_handlers) <= win_index and win_index >= 0:
            self.driver.switch_to.window(wnd_handlers[win_index])
        else:
            raise NoSuchWindowException("Invalid Window ID: %s" % str(win_index))

    def _switch_by_win_ser(self, window_name):
        if window_name == "win_ser_local":
            wnd_handlers = self.driver.window_handles
            if len(wnd_handlers) > 0:
                self.driver.switch_to.window(wnd_handlers[0])
            else:
                raise NoSuchWindowException("Invalid Window ID: %s" % window_name)
        else:
            if window_name not in self.windows:
                self.windows[window_name] = self.driver.window_handles[-1]
            self.driver.switch_to.window(self.windows[window_name])

    def close(self, window_name=None):
        if window_name:
            self.switch(window_name)
        self.driver.close()


class Manager:
    BYS = {
        'xpath': By.XPATH,
        'css': By.CSS_SELECTOR,
        'name': By.NAME,
        'id': By.ID,
        'linktext': By.LINK_TEXT
    }

    def get_locator(self, locators, ignore_implicit_wait=False):
        """
        :param locators: List of Dictionaries holding the locators, e.g. [{'id': 'elem_id'},
        {css: 'my_cls'}]
        :param ignore_implicit_wait: set it to True to set the implicit wait immediately to 0
        :return: first valid locator from the passed List, if no locator is valid then returns the
        first one
        """
        driver = _get_driver()
        timeout = _get_timeout()
        first_locator = None
        if ignore_implicit_wait:
            driver.implicitly_wait(0)
        for locator in locators:
            locator_type = list(locator.keys())[0]
            locator_value = locator[locator_type]
            if not first_locator:
                first_locator = (self.BYS[locator_type.lower()], locator_value)
            else:
                # set implicit wait to 0 get the result instantly for the other locators
                driver.implicitly_wait(0)
            elements = driver.find_elements(self.BYS[locator_type.lower()], locator_value)
            if len(elements) > 0:
                locator = (self.BYS[locator_type.lower()], locator_value)
                break
        else:
            driver.implicitly_wait(timeout)
            msg = "Element not found: (%s, %s)" % first_locator
            raise NoSuchElementException(msg)

        # restore the implicit wait value
        driver.implicitly_wait(timeout)
        return locator


class WaitForManager:

    def __init__(self, driver, timeout=30):
        self.driver = driver
        self.timeout = timeout
        self.loc_mngr = Manager()

    POSITIVE_CONDS = [
        "present", "visible", "clickable"
    ]

    NEGATIVE_CONDS = [
        "notpresent", "notvisible", "notclickable"
    ]

    def wait_for(self, condition, locators, wait_timeout=10):
        if condition.lower() in self.POSITIVE_CONDS:
            self._wait_for_positive(condition.lower(), locators, wait_timeout)
        elif condition.lower() in self.NEGATIVE_CONDS:
            self._wait_for_negative(condition.lower(), locators, wait_timeout)

    def _wait_for_positive(self, condition, locators, wait_timeout):
        start_time = time.time()
        while True:
            locator = None
            try:
                locator = self.loc_mngr.get_locator(locators, True)
            except NoSuchElementException:
                pass
            if locator:
                element = None
                try:
                    element = WebDriverWait(self.driver, wait_timeout).until(self._get_until_cond(condition, locator))
                except TimeoutException:
                    pass
                if element:
                    return

            elapsed_time = time.time() - start_time
            if elapsed_time > wait_timeout:
                raise NoSuchElementException("Timeout occurred while waiting for '%s' condition" % condition)

    def _wait_for_negative(self, condition, locators, wait_timeout):
        present_locs = []
        for locator in locators:
            try:
                present_locs.append(self.loc_mngr.get_locator([locator], True))
            except NoSuchElementException:
                pass
        if not present_locs:
            return
        start_time = time.time()
        for locator in present_locs:
            elapsed_time = time.time() - start_time
            timeout = wait_timeout - elapsed_time
            WebDriverWait(self.driver, timeout).until_not(
                self._get_until_cond(condition, locator),
                message="Timeout occurred while waiting for element (%s=%s) to become '%s'" %
                        (locator[0], locator[1], condition))

    @staticmethod
    def _get_until_cond(condition, locator):
        loc_tuple = (locator[0], locator[1])
        if "clickable" in condition:
            return econd.element_to_be_clickable(loc_tuple)
        if "present" in condition:
            return econd.presence_of_element_located(loc_tuple)
        if "visible" in condition:
            return econd.visibility_of_element_located(loc_tuple)


class DialogsManager:
    """
    Provides additional methods for working with Dialogs that are not available in the Python Webdriver.
    These JavaScript functions are taken from the Java Selenium WebDriver repository
    """

    def __init__(self, driver, is_active):
        """

        :param driver: the WebDriver instance
        :param is_active: flag indicating whether DialogsManager is going to be utilized in this test run,
        if yes then the dialogs will be replaced
        """
        self.driver = driver
        self.is_active = is_active

    def replace_dialogs(self):
        """
        Replaces the standard JavaScript methods, i.e. 'window.confirm', 'window.alert' and 'window.prompt' with
        own implementation that stores the messages from the dialogs and also is capable of returning user defined
        values
        """
        if not self.is_active:
            return  # don't replace dialogs in case DialogsManager is not activated

        self.driver.execute_script("""
          if (window.__webdriverAlerts) { return; }
          window.__webdriverAlerts = [];
          window.alert = function(msg) { window.__webdriverAlerts.push(msg); };
          window.__webdriverConfirms = [];
          window.__webdriverNextConfirm = true;
          window.confirm = function(msg) {
            window.__webdriverConfirms.push(msg);
            var res = window.__webdriverNextConfirm;
            window.__webdriverNextConfirm = true;
            return res;
          };
          window.__webdriverPrompts = [];
          window.__webdriverNextPrompts = true;
          window.prompt = function(msg, def) {
            window.__webdriverPrompts.push(msg || def);
            var res = window.__webdriverNextPrompt;
            window.__webdriverNextPrompt = true;
            return res;
          };
        """)

    def get_next_confirm(self):
        """
        :return: the message from the last invocation of 'window.confirm'
        """
        return self.driver.execute_script("""
                 if (!window.__webdriverConfirms) { return null; }
                 return window.__webdriverConfirms.shift();
               """)

    def get_next_alert(self):
        """
        :return: the alert message from the last invocation of 'window.alert'
        """
        return self.driver.execute_script("""
                if (!window.__webdriverAlerts) { return null } 
                var t = window.__webdriverAlerts.shift(); 
                if (t) { t = t.replace(/\\n/g, ' '); }
                return t;
              """)

    def get_next_prompt(self):
        """
        :return: the message from the last invocation of 'window.prompt'
        """
        return self.driver.execute_script("""
                if (!window.__webdriverPrompts) { return null; }
                return window.__webdriverPrompts.shift();
              """)

    def answer_on_next_prompt(self, value):
        """
        :param value: The value to be used to answer the next 'window.prompt', if '#cancel' is provided then
        click on cancel button is simulated by returning null
        """
        if str(value).lower() == '#cancel':
            self.driver.execute_script("window.__webdriverNextPrompt = null")
        else:
            self.driver.execute_script("window.__webdriverNextPrompt = '%s';" % value)

    def set_next_confirm_state(self, value):
        """
        :param value: either '#ok' to click on OK button or '#cancel' to simulate click on Cancel button in the
        next 'window.confirm' method
        """
        if str(value).lower() == '#ok':
            confirm = 'true'
        else:
            confirm = 'false'
        self.driver.execute_script("window.__webdriverNextConfirm = %s;" % confirm)
