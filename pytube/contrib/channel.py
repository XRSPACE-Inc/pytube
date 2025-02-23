# -*- coding: utf-8 -*-
"""Module for interacting with a user's youtube channel."""
import json
import logging
from typing import Dict, Iterable, List, Optional, Tuple

from pytube import Playlist, YouTube, extract, request
from pytube.helpers import DeferredGeneratorList, cache, uniqueify

logger = logging.getLogger(__name__)


class Channel(Playlist):
    def __init__(self, url: str, proxies: Optional[Dict[str, str]] = None):
        """Construct a :class:`Channel <Channel>`.
        :param str url:
            A valid YouTube channel URL.
        :param proxies:
            (Optional) A dictionary of proxies to use for web requests.
        """
        super().__init__(url, proxies)

        self.channel_uri = extract.channel_name(url)

        self.channel_url = f"https://www.youtube.com{self.channel_uri}"

        self.videos_url = self.channel_url + "/videos"
        self.shorts_url = self.channel_url + "/shorts"
        self.live_url = self.channel_url + "/streams"
        self.playlists_url = self.channel_url + "/playlists"
        self.community_url = self.channel_url + "/community"
        self.featured_channels_url = self.channel_url + "/channels"
        self.about_url = self.channel_url + "/about"

        self._html_url = (
            self.videos_url
        )  # Videos will be preferred over short videos and live
        self._visitor_data = None

        # Possible future additions
        self._playlists_html = None
        self._community_html = None
        self._featured_channels_html = None
        self._about_html = None

    @property
    def channel_name(self):
        """Get the name of the YouTube channel.

        :rtype: str
        """
        return self.initial_data["metadata"]["channelMetadataRenderer"]["title"]

    @property
    def channel_id(self):
        """Get the ID of the YouTube channel.

        This will return the underlying ID, not the vanity URL.

        :rtype: str
        """
        return self.initial_data["metadata"]["channelMetadataRenderer"]["externalId"]

    @property
    def vanity_url(self):
        """Get the vanity URL of the YouTube channel.

        Returns None if it doesn't exist.

        :rtype: str
        """
        return self.initial_data["metadata"]["channelMetadataRenderer"].get(
            "vanityChannelUrl", None
        )  # noqa:E501

    @property
    def html_url(self):
        """Get the html url.

        :rtype: str
        """
        return self._html_url

    @html_url.setter
    def html_url(self, value):
        """Set the html url and clear the cache."""
        if self._html_url != value:
            self._html = None
            self._initial_data = None
            self.__class__.video_urls.fget.cache_clear()
            self.__class__.last_updated.fget.cache_clear()
            self.__class__.title.fget.cache_clear()
            self._html_url = value

    @property
    def html(self):
        """Get the html for the /videos or /shorts page.

        :rtype: str
        """
        if self._html:
            return self._html
        self._html = request.get(self.html_url)
        return self._html

    @property
    def playlists_html(self):
        """Get the html for the /playlists page.

        Currently unused for any functionality.

        :rtype: str
        """
        if self._playlists_html:
            return self._playlists_html
        else:
            self._playlists_html = request.get(self.playlists_url)
            return self._playlists_html

    @property
    def community_html(self):
        """Get the html for the /community page.

        Currently unused for any functionality.

        :rtype: str
        """
        if self._community_html:
            return self._community_html
        else:
            self._community_html = request.get(self.community_url)
            return self._community_html

    @property
    def featured_channels_html(self):
        """Get the html for the /channels page.

        Currently unused for any functionality.

        :rtype: str
        """
        if self._featured_channels_html:
            return self._featured_channels_html
        else:
            self._featured_channels_html = request.get(self.featured_channels_url)
            return self._featured_channels_html

    @property
    def about_html(self):
        """Get the html for the /about page.

        Currently unused for any functionality.

        :rtype: str
        """
        if self._about_html:
            return self._about_html
        else:
            self._about_html = request.get(self.about_url)
            return self._about_html

    def _build_continuation_url(self, continuation: str) -> Tuple[str, dict, dict]:
        """Helper method to build the url and headers required to request
        the next page of videos

        :param str continuation: Continuation extracted from the json response
            of the last page
        :rtype: Tuple[str, dict, dict]
        :returns: Tuple of an url and required headers for the next http
            request
        """
        return (
            (
                # was changed to this format (and post requests)
                # between 2022.11.06 and 2022.11.20
                "https://www.youtube.com/youtubei/v1/browse?key="
                f"{self.yt_api_key}"
            ),
            {
                "X-YouTube-Client-Name": "1",
                "X-YouTube-Client-Version": "2.20200720.00.02",
            },
            # extra data required for post request
            {
                "continuation": continuation,
                "context": {
                    "client": {
                        "clientName": "WEB",
                        "visitorData": self._visitor_data,
                        "clientVersion": "2.20200720.00.02",
                    }
                },
            },
        )

    def _extract_videos(self, raw_json: str) -> Tuple[List[str], Optional[str]]:
        """Extracts videos from a raw json page

        :param str raw_json: Input json extracted from the page or the last
            server response
        :rtype: Tuple[List[str], Optional[str]]
        :returns: Tuple containing a list of up to 100 video watch ids and
            a continuation token, if more videos are available
        """
        initial_data = json.loads(raw_json)
        # this is the json tree structure, if the json was extracted from
        # html
        try:
            # Possible tabs: Home, Videos, Shorts, Live, Playlists, Community, Channels, About
            active_tab = {}
            for tab in initial_data["contents"]["twoColumnBrowseResultsRenderer"][
                "tabs"
            ]:
                tab_url = tab["tabRenderer"]["endpoint"]["commandMetadata"][
                    "webCommandMetadata"
                ]["url"]
                if (
                    tab_url.rsplit("/", maxsplit=1)[-1]
                    == self.html_url.rsplit("/", maxsplit=1)[-1]
                ):
                    active_tab = tab
                    break

            # This is the json tree structure for videos, shorts and streams
            videos = active_tab["tabRenderer"]["content"]["richGridRenderer"][
                "contents"
            ]

            # This is the json tree structure of visitor data
            # It is necessary to send the visitorData together with the continuation token
            self._visitor_data = initial_data["responseContext"][
                "webResponseContextExtensionData"
            ]["ytConfigData"]["visitorData"]

        except (KeyError, IndexError, TypeError):
            try:
                # this is the json tree structure, if the json was directly sent
                # by the server in a continuation response
                important_content = initial_data[1]["response"][
                    "onResponseReceivedActions"
                ][0]["appendContinuationItemsAction"]["continuationItems"]
                videos = important_content
            except (KeyError, IndexError, TypeError):
                try:
                    # this is the json tree structure, if the json was directly sent
                    # by the server in a continuation response
                    # no longer a list and no longer has the "response" key
                    important_content = initial_data["onResponseReceivedActions"][0][
                        "appendContinuationItemsAction"
                    ]["continuationItems"]
                    videos = important_content
                except (KeyError, IndexError, TypeError) as p:
                    logger.info(p)
                    return [], None

        try:
            continuation = videos[-1]["continuationItemRenderer"][
                "continuationEndpoint"
            ]["continuationCommand"]["token"]
            videos = videos[:-1]
        except (KeyError, IndexError):
            # if there is an error, no continuation is available
            continuation = None

        # only extract the video ids from the video data
        videos_url = []
        try:
            # Extract id from videos and live
            for x in videos:
                videos_url.append(
                    f"/watch?v="
                    f"{x['richItemRenderer']['content']['videoRenderer']['videoId']}"
                )
        except (KeyError, IndexError, TypeError):
            # Extract id from short videos
            for x in videos:
                videos_url.append(
                    f"/watch?v="
                    f"{x['richItemRenderer']['content']['reelItemRenderer']['videoId']}"
                )

        # remove duplicates
        return uniqueify(videos_url), continuation

    @property
    def views(self) -> int:
        """Extract view count for channel.

        :return: Channel view count
        :rtype: int
        """
        self.html_url = self.about_url
        # Get the position of the "about" tab.
        about_tab_pos = (
            len(self.initial_data["contents"]["twoColumnBrowseResultsRenderer"]["tabs"])
            - 2
        )
        try:
            views_text = self.initial_data["contents"][
                "twoColumnBrowseResultsRenderer"
            ]["tabs"][about_tab_pos]["tabRenderer"]["content"]["sectionListRenderer"][
                "contents"
            ][
                0
            ][
                "itemSectionRenderer"
            ][
                "contents"
            ][
                0
            ][
                "channelAboutFullMetadataRenderer"
            ][
                "viewCountText"
            ][
                "simpleText"
            ]

            # "1,234,567 view"
            count_text = views_text.split(" ")[0]
            # "1234567"
            count_text = count_text.replace(",", "")
            return int(count_text)
        except KeyError:
            return 0

    @property
    @cache
    def title(self) -> str:
        """Extract the channel title.

        :return: Channel title (name)
        :rtype: str
        """
        self.html_url = self.channel_url
        return self.initial_data["metadata"]["channelMetadataRenderer"]["title"]

    @property
    def description(self) -> str:
        """Extract the channel description.

        :return: Channel description
        :rtype: str
        """
        self.html_url = self.channel_url
        return self.initial_data["metadata"]["channelMetadataRenderer"]["description"]

    @property
    def length(self):
        """Extracts the approximate amount of videos from the channel.

        :return: Channel videos count
        :rtype: str
        """
        self.html_url = self.channel_url
        return self.initial_data["header"]["c4TabbedHeaderRenderer"]["videosCountText"][
            "runs"
        ][0]["text"]

    @property
    @cache
    def last_updated(self):
        """Extract the date of the last uploaded video.

        :return: Last video uploaded
        :rtype: str
        """
        self.html_url = self.videos_url
        try:
            last_updated_text = self.initial_data["contents"][
                "twoColumnBrowseResultsRenderer"
            ]["tabs"][1]["tabRenderer"]["content"]["richGridRenderer"]["contents"][0][
                "richItemRenderer"
            ][
                "content"
            ][
                "videoRenderer"
            ][
                "publishedTimeText"
            ][
                "simpleText"
            ]
            return last_updated_text
        except KeyError:
            return None

    @property
    def videos(self) -> Iterable[YouTube]:
        """Yields YouTube objects of videos in this channel

        :rtype: List[YouTube]
        :returns: List of YouTube
        """
        self.html_url = self.videos_url  # Set video tab
        return DeferredGeneratorList(self.videos_generator())

    @property
    def shorts(self) -> Iterable[YouTube]:
        """Yields YouTube objects of short videos in this channel

        :rtype: List[YouTube]
        :returns: List of YouTube
        """
        self.html_url = self.shorts_url  # Set shorts tab
        return DeferredGeneratorList(self.videos_generator())

    @property
    def live(self) -> Iterable[YouTube]:
        """Yields YouTube objects of live in this channel

        :rtype: List[YouTube]
        :returns: List of YouTube
        """
        self.html_url = self.live_url  # Set stream tab
        return DeferredGeneratorList(self.videos_generator())
