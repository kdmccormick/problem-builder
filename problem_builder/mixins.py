import json

import webob
from lazy import lazy
from problem_builder.tests.unit.utils import DateTimeEncoder
from xblock.core import XBlock
from xblock.fields import String, Boolean, Float, Scope, UNIQUE_ID
from xblock.fragment import Fragment
from xblockutils.helpers import child_isinstance
from xblockutils.resources import ResourceLoader


loader = ResourceLoader(__name__)


# Make '_' a no-op so we can scrape strings
def _(text):
    return text


def _normalize_id(key):
    """
    Helper method to normalize a key to avoid issues where some keys have version/branch and others don't.
    e.g. self.scope_ids.usage_id != self.runtime.get_block(self.scope_ids.usage_id).scope_ids.usage_id
    """
    if hasattr(key, "for_branch"):
        key = key.for_branch(None)
    if hasattr(key, "for_version"):
        key = key.for_version(None)
    return key


class XBlockWithTranslationServiceMixin(object):
    """
    Mixin providing access to i18n service
    """
    def _(self, text):
        """ Translate text """
        return self.runtime.service(self, "i18n").ugettext(text)


class EnumerableChildMixin(XBlockWithTranslationServiceMixin):
    CAPTION = _(u"Child")

    show_title = Boolean(
        display_name=_("Show title"),
        help=_("Display the title?"),
        default=True,
        scope=Scope.content
    )

    @lazy
    def siblings(self):
        # TODO: It might make sense to provide a default
        # implementation here that just returns normalized ID's of the
        # parent's children.
        raise NotImplementedError("Should be overridden in child class")

    @lazy
    def step_number(self):
        return list(self.siblings).index(_normalize_id(self.scope_ids.usage_id)) + 1

    @lazy
    def lonely_child(self):
        if _normalize_id(self.scope_ids.usage_id) not in self.siblings:
            message = u"{child_caption}'s parent should contain {child_caption}".format(child_caption=self.CAPTION)
            raise ValueError(message, self, self.siblings)
        return len(self.siblings) == 1

    @property
    def display_name_with_default(self):
        """ Get the title/display_name of this question. """
        if self.display_name:
            return self.display_name
        if not self.lonely_child:
            return self._(u"{child_caption} {number}").format(
                child_caption=self.CAPTION, number=self.step_number
            )
        return self._(self.CAPTION)


class StepParentMixin(object):
    """
    An XBlock mixin for a parent block containing Step children
    """

    @lazy
    def step_ids(self):
        """
        Get the usage_ids of all of this XBlock's children that are "Steps"
        """
        return [
            _normalize_id(child_id) for child_id in self.children if child_isinstance(self, child_id, QuestionMixin)
        ]

    @lazy
    def steps(self):
        """ Get the step children of this block, cached if possible. """
        return [self.runtime.get_block(child_id) for child_id in self.step_ids]


class MessageParentMixin(object):
    """
    An XBlock mixin for a parent block containing MentoringMessageBlock children
    """

    def get_message_content(self, message_type, or_default=False):
        from problem_builder.message import MentoringMessageBlock  # Import here to avoid circular dependency
        for child_id in self.children:
            if child_isinstance(self, child_id, MentoringMessageBlock):
                child = self.runtime.get_block(child_id)
                if child.type == message_type:
                    content = child.content
                    if getattr(self.runtime, 'replace_jump_to_id_urls', None) is not None:
                        content = self.runtime.replace_jump_to_id_urls(content)
                    return content
        if or_default:
            # Return the default value since no custom message is set.
            # Note the WYSIWYG editor usually wraps the .content HTML in a <p> tag so we do the same here.
            return '<p>{}</p>'.format(MentoringMessageBlock.MESSAGE_TYPES[message_type]['default'])


class QuestionMixin(EnumerableChildMixin):
    """
    An XBlock mixin for a child block that is a "Step".

    A step is a question that the user can answer (as opposed to a read-only child).
    """
    CAPTION = _(u"Question")

    has_author_view = True

    # Fields:
    name = String(
        display_name=_("Question ID (name)"),
        help=_("The ID of this question (required). Should be unique within this mentoring component."),
        default=UNIQUE_ID,
        scope=Scope.settings,  # Must be scope.settings, or the unique ID will change every time this block is edited
    )
    display_name = String(
        display_name=_("Question title"),
        help=_('Leave blank to use the default ("Question 1", "Question 2", etc.)'),
        default="",  # Blank will use 'Question x' - see display_name_with_default
        scope=Scope.content
    )
    weight = Float(
        display_name=_("Weight"),
        help=_("Defines the maximum total grade of this question."),
        default=1,
        scope=Scope.content,
        enforce_type=True
    )

    @lazy
    def siblings(self):
        return self.get_parent().step_ids

    def author_view(self, context):
        context = context.copy() if context else {}
        context['hide_header'] = True
        return self.mentoring_view(context)

    def author_preview_view(self, context):
        context = context.copy() if context else {}
        context['hide_header'] = True
        return self.student_view(context)

    def assessment_step_view(self, context=None):
        """
        assessment_step_view is the same as mentoring_view, except its DIV will have a different
        class (.xblock-v1-assessment_step_view) that we use for assessments to hide all the
        steps with CSS and to detect which children of mentoring are "Steps" and which are just
        decorative elements/instructions.
        """
        return self.mentoring_view(context)


class NoSettingsMixin(object):
    """ Mixin for an XBlock that has no settings """

    def studio_view(self, _context=None):
        """ Studio View """
        return Fragment(u'<p>{}</p>'.format(self._("This XBlock does not have any settings.")))


class StudentViewUserStateMixin(object):
    """
    Mixin to provide student_view_user_state view.

    To prevent unnecessary overloading of the build_user_state_data method,
    you may specify `USER_STATE_FIELDS` to customise build_user_state_data
    and student_view_user_state output.
    """
    NESTED_BLOCKS_KEY = "components"
    INCLUDE_SCOPES = (Scope.user_state, Scope.user_info, Scope.preferences)
    USER_STATE_FIELDS = []

    def transforms(self):
        """
        Return a dict where keys are fields to transform, and values are
        transform functions that accept a value to to transform as the
        only argument.
        """
        return {}

    def build_user_state_data(self, context=None):
        """
        Returns a dictionary of the student data of this XBlock,
        retrievable from the Course Block API.
        """

        result = {}
        transforms = self.transforms()
        for _, field in self.fields.iteritems():
            # Only insert fields if their scopes and field names match
            if field.scope in self.INCLUDE_SCOPES and field.name in self.USER_STATE_FIELDS:
                transformer = transforms.get(field.name, lambda value: value)
                result[field.name] = transformer(field.read_from(self))

        if getattr(self, "has_children", False):
            components = {}
            for child_id in self.children:
                child = self.runtime.get_block(child_id)
                if hasattr(child, 'build_user_state_data'):
                    components[str(child_id)] = child.build_user_state_data(context)

            result[self.NESTED_BLOCKS_KEY] = components

        return result

    @XBlock.handler
    def student_view_user_state(self, context=None, suffix=''):
        """
        Returns a JSON representation of the student data of this XBlock,
        retrievable from the Course Block API.
        """
        result = self.build_user_state_data(context)
        json_result = json.dumps(result, cls=DateTimeEncoder)

        return webob.response.Response(body=json_result, content_type='application/json')


class StudentViewUserStateResultsTransformerMixin(object):
    """
    A convenient way for MentoringBlock and MentoringStepBlock to share
    student_results transform code.
    Needs to be used alongside StudentViewUserStateMixin.
    """
    def transforms(self):
        return {
            'student_results': self.transform_student_results
        }

    def transform_student_results(self, student_results):
        """
        Remove tips, since they are already in student_view_data.
        """
        for _name, current_student_results in student_results:
            for choice in current_student_results.get('choices', []):
                self.delete_key(choice, 'tips')
            self.delete_key(current_student_results, 'tips')

        return student_results

    def delete_key(self, dictionary, key):
        """
        Safely delete `key` from `dictionary`.
        """
        try:
            del dictionary[key]
        except KeyError:
            pass
        return dictionary
