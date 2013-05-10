import json
import logging as log
from django.contrib.syndication.views import Feed as DjangoFeed
from django.shortcuts import get_object_or_404
from django.views import generic as views
from haystack.query import SearchQuerySet
import datetime
from datetime import timedelta

from cm_api.resources import SubscriberResource
from main import feeds
from main import forms

import haystack.views
import bookmarks.views
import opinions.views
import phillyleg.models
import subscriptions.forms
import subscriptions.models
import subscriptions.views


class NewLegislationFeed (DjangoFeed):
    title = u'New Legislation'
    link = 'http://localhost:8000'
    description = u'Newly introduced legislation'
    max_items = 100

    def items(self):
        return phillyleg.models.LegFile.objects.all()[:self.max_items]

    def item_title(self, legfile):
        return u'{0.type} {0.id}'.format(legfile)

    def item_pubdate(self, legfile):
        import datetime
        d = legfile.intro_date
        current = datetime.datetime(d.year, d.month, d.day)
        return current


class CustomContentFeedView (DjangoFeed):
    def get_object(self, request, subscription_id):
        return get_object_or_404(subscription.models.Subscription,
                                 pk=subscription_id)

    def title(self, sub):
        return unicode(sub)

    def link(self, sub):
#        return sub.get_absolute_url()
        return u'http://localhost:8000/'

    def description(self, sub):
        return unicode(sub)


class SearchBarMixin (object):
    def get_searchbar_form(self):
        return forms.SimpleSearchForm()

    def get_context_data(self, **kwargs):
        context_data = super(SearchBarMixin, self).get_context_data(**kwargs)
        context_data.update({'searchbar_form': self.get_searchbar_form()})
        return context_data


class BaseDashboardMixin (SearchBarMixin,
                          bookmarks.views.BaseBookmarkMixin):

    def get_recent_legislation(self):
        legfiles = self.get_filtered_legfiles()
        return list(legfiles.exclude(metadata__topics__topic='Routine').order_by('-key')[:6])

    def get_context_data(self, **kwargs):
        search_form = forms.FullSearchForm()

        legfiles = self.get_recent_legislation()
        bookmark_data = self.get_bookmarks_data(legfiles)
        bookmark_cache_key = self.get_bookmarks_cache_key(bookmark_data)

        context_data = super(BaseDashboardMixin, self).get_context_data(
            **kwargs)
        context_data.update({
            'legfiles': legfiles,
            'bookmark_data': bookmark_data,
            'bookmark_cache_key': bookmark_cache_key,
            'search_form': search_form,
        })

        return context_data


class AppDashboardView (BaseDashboardMixin,
                        views.TemplateView):
    template_name = 'councilmatic/dashboard.html'

    def get_filtered_legfiles(self):
        return phillyleg.models.LegFile.objects

    def get_recent_locations(self):
        return list(phillyleg.models.MetaData_Location.objects.\
                       all().filter(valid=True).order_by('-pk')[:10].\
                       prefetch_related('references_in_legislation'))

    def get_recent_topics(self):
        # get the month weeks of legislation
        now = datetime.date.today()
        one_month = datetime.timedelta(days=31)
        date_string = now-one_month

        topic_count_query = """SELECT phillyleg_metadata_topic.id, phillyleg_metadata_topic.topic, Count(phillyleg_legfilemetadata.legfile_id) AS leg_count FROM phillyleg_metadata_topic
        JOIN phillyleg_legfilemetadata_topics ON phillyleg_legfilemetadata_topics.metadata_topic_id = phillyleg_metadata_topic.id
        JOIN phillyleg_legfilemetadata ON phillyleg_legfilemetadata.id = phillyleg_legfilemetadata_topics.legfilemetadata_id
        JOIN phillyleg_legfile ON phillyleg_legfile.key = phillyleg_legfilemetadata.legfile_id
        WHERE phillyleg_legfile.intro_date > '{date_string}' AND phillyleg_metadata_topic.topic != 'Routine'
        GROUP BY phillyleg_metadata_topic.topic, phillyleg_metadata_topic.id
        ORDER BY leg_count DESC""".format(date_string=date_string)

        return phillyleg.models.MetaData_Topic.objects.raw(topic_count_query)


    def get_context_data(self, **kwargs):

        recent_topics_query = self.get_recent_topics()
        recent_topics = []

        for t in recent_topics_query:
            percent_width = 100 * (float(t.leg_count) / float(recent_topics_query[0].leg_count))
            recent_topics.append({'topic': t.topic, 'leg_count': t.leg_count, 'percent_width': percent_width})

        context_data = super(AppDashboardView, self).get_context_data(**kwargs)
        context_data['recent_topics'] = recent_topics
        return context_data


class CouncilMemberDetailView (BaseDashboardMixin,
                               subscriptions.views.SingleSubscriptionMixin,
                               views.DetailView):
    queryset = phillyleg.models.CouncilMember.objects.prefetch_related('tenures', 'tenures__district')
    template_name = 'councilmatic/councilmember_detail.html'

    def get_content_feed(self):
        return feeds.SearchResultsFeed(search_filter={'sponsors': [self.object.name]})

    def get_filtered_legfiles(self):
        return self.object.legislation

    def get_district(self):
        return self.object.district

    def get_topics(self):
        topic_count_query = """SELECT phillyleg_metadata_topic.id, phillyleg_metadata_topic.topic, Count(phillyleg_legfilemetadata.legfile_id) AS leg_count FROM phillyleg_metadata_topic
        JOIN phillyleg_legfilemetadata_topics ON phillyleg_legfilemetadata_topics.metadata_topic_id = phillyleg_metadata_topic.id
        JOIN phillyleg_legfilemetadata ON phillyleg_legfilemetadata.id = phillyleg_legfilemetadata_topics.legfilemetadata_id
        JOIN phillyleg_legfile ON phillyleg_legfile.key = phillyleg_legfilemetadata.legfile_id
        JOIN phillyleg_legfile_sponsors ON phillyleg_legfile_sponsors.legfile_id = phillyleg_legfile.key
        WHERE phillyleg_metadata_topic.topic != 'Routine' AND phillyleg_legfile_sponsors.councilmember_id = {sponsor_id}
        GROUP BY phillyleg_metadata_topic.topic, phillyleg_metadata_topic.id
        ORDER BY leg_count DESC""".format(sponsor_id=40)

        return phillyleg.models.MetaData_Topic.objects.raw(topic_count_query)

    def get_context_data(self, **kwargs):
        district = self.get_district()
        context_data = super(CouncilMemberDetailView, self).get_context_data(**kwargs)
        context_data['district'] = district
        context_data['recent_topics'] = self.get_topics()
        return context_data


class SearcherMixin (object):
    def _init_haystack_searchview(self, request):
        # Construct and run a haystack SearchView so that we can use the
        # resulting values.
        self.search_view = haystack.views.SearchView(form_class=forms.FullSearchForm, searchqueryset=SearchQuerySet())
        self.search_view.request = request

        self.search_view.form = self.search_view.build_form()
        self.search_view.query = self.search_view.get_query()
        self.search_view.results = self.search_view.get_results().order_by('-order_date')

    def _get_search_results(self, query_params):
        if len(query_params) == 0:
            search_queryset = phillyleg.models.LegFile.objects.all().order_by('-key')

        else:
            class SQSProxy (object):
                """
                Make a SearchQuerySet look enough like a QuerySet for a ListView
                not to notice the difference.
                """
                def __init__(self, sqs):
                    self.sqs = sqs
                def __len__(self):
                    return len(self.sqs)
                def __iter__(self):
                    return (result.object for result in self.sqs)
                def __getitem__(self, key):
                    if isinstance(key, slice):
                        return [result.object for result in self.sqs[key] if result is not None]
                    else:
                        return self.sqs[key].object

            search_queryset = SQSProxy(self.search_view.results)
        return search_queryset


class LegFileListFeedView (SearcherMixin, DjangoFeed):
    def get_object(self, request, *args, **kwargs):
        self._init_haystack_searchview(request)
        query_params = request.GET.copy()
        search_queryset = self._get_search_results(query_params)
        return search_queryset

    def items(self, obj):
        return obj[:100]

    def title(self, obj):
        'testing'

    def link(self):
        return 'http://www.google.com/'


class SearchView (SearcherMixin,
                  SearchBarMixin,
                  subscriptions.views.SingleSubscriptionMixin,
                  views.ListView):
    template_name = 'councilmatic/search.html'
    paginate_by = 20
    feed_data = None

    def dispatch(self, request, *args, **kwargs):
        self._init_haystack_searchview(request)
        return super(SearchView, self).dispatch(request, *args, **kwargs)

    def get_content_feed(self):
        search_params = self.request.GET
        return feeds.SearchResultsFeed(search_filter=search_params)

    def get_queryset(self):
        query_params = self.request.GET.copy()
        if 'page' in query_params:
            del query_params['page']
        search_queryset = self._get_search_results(query_params)
        return search_queryset

    def get_context_data(self, **kwargs):
        """
        Generates the actual HttpResponse to send back to the user.
        """
        context = super(SearchView, self).get_context_data(**kwargs)
        context['form'] = self.search_view.form

        page_obj = context.get('page_obj', None)
        query_params = self.request.GET.copy()
        if 'page' in query_params:
            del query_params['page']

        if page_obj:
            context['first_url'] = self.paginated_url(
                1, query_params)

            context['last_url'] = self.paginated_url(
                page_obj.paginator.num_pages, query_params)

            if page_obj.has_next():
                context['next_url'] = self.paginated_url(
                    page_obj.next_page_number(), query_params)

            if page_obj.has_previous():
                context['previous_url'] = self.paginated_url(
                    page_obj.previous_page_number(), query_params)

            page_urls = []
            start_num = max(1, min(page_obj.number - 5,
                                   page_obj.paginator.num_pages - 9))
            end_num = min(start_num + 10, page_obj.paginator.num_pages + 1)

            for page_num in range(start_num, end_num):
                if page_num != page_obj.number:
                    url = self.paginated_url(page_num, query_params)
                else:
                    url = None
                page_urls.append((page_num, url))
            context['page_urls'] = page_urls

        log.debug(context)
        return context

    def paginated_url(self, page_num, query_params):
        url = '{0}?page={1}'.format(self.request.path, page_num)
        if query_params:
            url += '&' + query_params.urlencode()
        return url


class LegislationStatsMixin (object):
    def get_queryset(self):
        queryset = super(LegislationStatsMixin, self).get_queryset()

        now = datetime.date.today()
        four_weeks = datetime.timedelta(days=28)
        queryset = queryset.filter(intro_date__gte=now-four_weeks)
        return queryset


class LegislationListView (SearchBarMixin,
                           subscriptions.views.SingleSubscriptionMixin,
                           bookmarks.views.MultipleBookmarkedObjectsMixin,
                           views.ListView):
    model = phillyleg.models.LegFile
    template_name = 'phillyleg/legfile_list.html'
    paginate_by = 10

    def get_content_feed(self):
        return feeds.NewLegislationFeed()

    def get_queryset(self):
        queryset = super(LegislationListView, self).get_queryset()
        return queryset.order_by('-intro_date', '-key')


class LegislationDetailView (SearchBarMixin,
                             subscriptions.views.SingleSubscriptionMixin,
                             bookmarks.views.SingleBookmarkedObjectMixin,
                             opinions.views.SingleOpinionTargetMixin,
                             views.DetailView):
    model = phillyleg.models.LegFile
    template_name = 'councilmatic/legfile_detail.html'

    def get_queryset(self):
        """Select all the data relevant to the legislation."""
        return self.model.objects\
                   .all().select_related('metadata')\
                   .prefetch_related('actions', 'attachments', 'sponsors',
                                     'references_in_legislation',
                                     'metadata__locations',
                                     'metadata__mentioned_legfiles',
                                     'metadata__topics')

    def get_content_feed(self):
        legfile = self.object
        return feeds.LegislationUpdatesFeed(pk=legfile.pk)

#    def on_object_gotten(self, legfile):
#        # Construct the feed_data factory
#        if not self.feed_data:
#            self.feed_data = lambda: feeds.LegislationUpdatesFeed(pk=legfile.pk)

#    def get_object(self):
#        legfile = super(LegislationDetailView, self).get_object()

#        # Intercept the object
#        self.on_object_gotten(legfile)

#        return legfile


class BookmarkListView (SearchBarMixin,
                        views.ListView):
    template_name = 'main/bookmark_list.html'

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated():
            return [bm.content for bm in user.bookmarks.all()]
        else:
            return []
